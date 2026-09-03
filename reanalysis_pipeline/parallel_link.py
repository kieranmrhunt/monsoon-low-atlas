#!/usr/bin/env python3
"""Link multi-decadal reanalysis candidates in overlapping annual blocks.

The v5.6 production catalogue uses one core-year block at a time, with the
preceding December and following January as halos.  Adjacent blocks are then
reconciled from shared observed candidate identities.  Reanalysis backfills use
the same strategy here so runtime grows linearly and the work can be spread
across Slurm without breaking tracks at calendar-year boundaries.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from .common import sha256
from .track import LINKER, PARAMETERS


SCHEMA = "lps-atlas-reanalysis-parallel-link-v1"
MONTH_PATTERN = re.compile(r"^candidates-(\d{6})\.csv$")
SOURCE_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
MAXIMUM_HOURLY_STEP_KM = 150.0


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def atomic_csv_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty task manifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def candidate_inventory(output_root: Path) -> dict[str, Path]:
    inventory: dict[str, Path] = {}
    for path in sorted((output_root / "candidates").glob("candidates-*.csv")):
        match = MONTH_PATTERN.match(path.name)
        if match is None:
            continue
        month = match.group(1)
        if month in inventory:
            raise ValueError(f"duplicate candidate month {month}")
        if not path.is_file() or path.stat().st_size < 100:
            raise ValueError(f"invalid candidate file: {path}")
        inventory[month] = path.resolve()
    if not inventory:
        raise FileNotFoundError(f"no candidate files below {output_root / 'candidates'}")
    expected = [period.strftime("%Y%m") for period in pd.period_range(min(inventory), max(inventory), freq="M")]
    missing = sorted(set(expected) - set(inventory))
    if missing:
        raise ValueError(f"candidate collection has missing months; first: {missing[:12]}")
    return inventory


def validate_source_label(source: str) -> str:
    """Return a filesystem-safe source label usable by any frozen-v5.6 run."""

    if not SOURCE_LABEL_PATTERN.fullmatch(source):
        raise ValueError(
            "source must be 1--192 characters and contain only letters, numbers, '.', '_' or '-'"
        )
    return source


def block_months(inventory: dict[str, Path], core_year: int) -> list[str]:
    selected = [
        month
        for month in inventory
        if (int(month[:4]) == core_year)
        or month == f"{core_year - 1:04d}12"
        or month == f"{core_year + 1:04d}01"
    ]
    return sorted(selected)


def prepare(source: str, output_root: Path, run_root: Path, *, force: bool = False) -> Path:
    source = validate_source_label(source)
    output_root = output_root.resolve()
    run_root = run_root.resolve()
    inventory = candidate_inventory(output_root)
    years = range(int(min(inventory)[:4]), int(max(inventory)[:4]) + 1)
    rows: list[dict[str, Any]] = []
    for task_id, year in enumerate(years):
        selected = block_months(inventory, year)
        if not any(month.startswith(f"{year:04d}") for month in selected):
            continue
        task_dir = run_root / "tasks" / str(year)
        candidate_list = task_dir / "candidate-list.txt"
        atomic_text(candidate_list, "".join(f"{inventory[month]}\n" for month in selected))
        rows.append({
            "task_id": len(rows),
            "core_year": year,
            "first_month": selected[0],
            "last_month": selected[-1],
            "month_count": len(selected),
            "candidate_list": str(candidate_list),
            "candidate_list_sha256": sha256(candidate_list),
            "task_dir": str(task_dir),
            "tracks": str(task_dir / "tracks.csv"),
        })
    manifest_path = run_root / "task-manifest.csv"
    if manifest_path.exists() and not force:
        previous = pd.read_csv(manifest_path)
        expected = pd.DataFrame(rows)
        comparable = ["task_id", "core_year", "first_month", "last_month", "month_count"]
        if previous[comparable].equals(expected[comparable]):
            return manifest_path
        raise FileExistsError(f"prepared task manifest differs: {manifest_path}")
    atomic_csv_rows(manifest_path, rows)
    files = [
        {
            "month": month,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for month, path in inventory.items()
    ]
    atomic_json(run_root / "input-manifest.json", {
        "schema": SCHEMA,
        "status": "prepared",
        "prepared_utc": utc_now(),
        "source": source,
        "candidate_months": list(inventory),
        "candidate_files": files,
        "candidate_bytes": sum(item["bytes"] for item in files),
        "linker": {"path": str(LINKER), "sha256": sha256(LINKER)},
        "parameters": {"path": str(PARAMETERS), "sha256": sha256(PARAMETERS)},
        "task_manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
        "blocks": len(rows),
        "method": "Annual core blocks with preceding-December/following-January halos and shared-candidate identity reconciliation.",
    })
    return manifest_path


def manifest_rows(run_root: Path) -> list[dict[str, str]]:
    manifest = run_root.resolve() / "task-manifest.csv"
    if not manifest.is_file():
        raise FileNotFoundError(manifest)
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    task_ids = [int(row["task_id"]) for row in rows]
    if not rows or task_ids != list(range(len(rows))):
        raise ValueError("parallel-link task IDs must be non-empty and contiguous")
    return rows


def selected_task(run_root: Path, task_id: int) -> dict[str, str]:
    rows = manifest_rows(run_root)
    if task_id < 0 or task_id >= len(rows):
        raise IndexError(f"task {task_id} is outside 0..{len(rows) - 1}")
    return rows[task_id]


def worker(run_root: Path, task_id: int, *, force: bool = False) -> Path:
    row = selected_task(run_root, task_id)
    task_dir = Path(row["task_dir"])
    status = task_dir / "status.json"
    tracks = Path(row["tracks"])
    if status.is_file() and tracks.is_file() and not force:
        value = json.loads(status.read_text(encoding="utf-8"))
        if value.get("status") == "complete" and value.get("tracks_sha256") == sha256(tracks):
            return tracks
    candidate_list = Path(row["candidate_list"])
    if sha256(candidate_list) != row["candidate_list_sha256"]:
        raise RuntimeError(f"candidate list changed for task {task_id}")
    command = [
        sys.executable,
        str(LINKER),
        "--candidate-list", str(candidate_list),
        "--params", str(PARAMETERS),
        "--out", str(tracks),
        "--rejected-out", str(task_dir / "rejected.csv"),
        "--links-out", str(task_dir / "links.csv"),
        "--summary-out", str(task_dir / "summary.json"),
        "--tuning-light",
        "--verbose",
    ]
    started = time.monotonic()
    atomic_json(status, {"schema": SCHEMA, "status": "running", "started_utc": utc_now(), "command": command})
    try:
        subprocess.run(command, cwd=LINKER.parent, check=True)
    except Exception as error:
        atomic_json(status, {
            "schema": SCHEMA,
            "status": "failed",
            "updated_utc": utc_now(),
            "elapsed_seconds": time.monotonic() - started,
            "command": command,
            "error": repr(error),
        })
        raise
    outputs = {
        name: task_dir / filename
        for name, filename in {
            "tracks": "tracks.csv",
            "rejected": "rejected.csv",
            "links": "links.csv",
            "summary": "summary.json",
        }.items()
    }
    for path in outputs.values():
        if not path.is_file():
            raise RuntimeError(f"link worker omitted {path}")
    atomic_json(status, {
        "schema": SCHEMA,
        "status": "complete",
        "completed_utc": utc_now(),
        "elapsed_seconds": time.monotonic() - started,
        "command": command,
        **{f"{name}_sha256": sha256(path) for name, path in outputs.items()},
    })
    return tracks


class DisjointSet:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def observed_identities(path: Path, year: int) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["candidate_uid", "track_id", "position_source"], dtype={"candidate_uid": str, "track_id": str})
    frame = frame.loc[frame["position_source"].astype(str).eq("observed")].copy()
    if frame["candidate_uid"].duplicated().any():
        raise ValueError(f"duplicate observed candidate identity within {path}")
    frame["node"] = f"{year}:" + frame["track_id"].astype(str)
    return frame[["candidate_uid", "node"]]


def completed_rows(run_root: Path) -> list[dict[str, str]]:
    rows = manifest_rows(run_root)
    for row in rows:
        task_dir = Path(row["task_dir"])
        status_path = task_dir / "status.json"
        tracks = Path(row["tracks"])
        if not status_path.is_file() or not tracks.is_file():
            raise RuntimeError(f"parallel-link block {row['core_year']} is incomplete")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "complete" or status.get("tracks_sha256") != sha256(tracks):
            raise RuntimeError(f"parallel-link block {row['core_year']} failed validation")
    return rows


def reconcile_blocks(rows: Iterable[dict[str, str]]) -> tuple[DisjointSet, list[dict[str, Any]]]:
    identities = DisjointSet()
    audit: list[dict[str, Any]] = []
    previous_year: int | None = None
    previous: pd.DataFrame | None = None
    for row in rows:
        year = int(row["core_year"])
        current = observed_identities(Path(row["tracks"]), year)
        for node in current["node"].unique():
            identities.find(str(node))
        if previous is not None and previous_year is not None:
            shared = previous.merge(current, on="candidate_uid", suffixes=("_left", "_right"), how="inner")
            counts = shared.groupby(["node_left", "node_right"]).size().rename("count").reset_index()
            left_nodes = sorted(counts["node_left"].unique())
            right_nodes = sorted(counts["node_right"].unique())
            selected_pairs = selected_uids = 0
            if left_nodes and right_nodes:
                matrix = np.zeros((len(left_nodes), len(right_nodes)), dtype=np.int64)
                left_index = {value: index for index, value in enumerate(left_nodes)}
                right_index = {value: index for index, value in enumerate(right_nodes)}
                for item in counts.itertuples(index=False):
                    matrix[left_index[item.node_left], right_index[item.node_right]] = int(item.count)
                row_indices, column_indices = linear_sum_assignment(-matrix)
                for row_index, column_index in zip(row_indices, column_indices):
                    weight = int(matrix[row_index, column_index])
                    if weight <= 0:
                        continue
                    identities.union(left_nodes[row_index], right_nodes[column_index])
                    selected_pairs += 1
                    selected_uids += weight
            audit.append({
                "left_year": previous_year,
                "right_year": year,
                "shared_candidate_uids": len(shared),
                "candidate_track_pairs": len(counts),
                "selected_one_to_one_pairs": selected_pairs,
                "selected_pair_shared_uids": selected_uids,
            })
        previous_year, previous = year, current
    return identities, audit


def core_frame(row: dict[str, str]) -> tuple[pd.DataFrame, pd.Series]:
    frame = pd.read_csv(row["tracks"], dtype={"candidate_uid": str})
    timestamps = pd.to_datetime(frame["time"], errors="raise", utc=True)
    selected = timestamps.dt.year.eq(int(row["core_year"]))
    return frame.loc[selected].copy(), timestamps.loc[selected]


def _haversine_km(left_lon: float, left_lat: float, right_lon: float, right_lat: float) -> float:
    values = np.asarray((left_lon, left_lat, right_lon, right_lat), dtype=float)
    if not np.isfinite(values).all():
        return float("nan")
    left_lon_r, left_lat_r, right_lon_r, right_lat_r = np.deg2rad(values)
    dlon = right_lon_r - left_lon_r
    dlat = right_lat_r - left_lat_r
    value = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(left_lat_r) * np.cos(right_lat_r) * np.sin(dlon / 2.0) ** 2
    )
    return float(2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0))))


def assign_continuous_track_ids(
    frame: pd.DataFrame,
    timestamps: pd.Series,
    roots: pd.Series,
    active_ids: dict[str, int],
    last_positions: dict[str, tuple[pd.Timestamp, float, float]],
    next_track_id: int,
    *,
    block_year: int,
) -> tuple[pd.Series, int, list[dict[str, Any]]]:
    """Split a reconciled identity if adjacent core blocks do not join physically.

    Shared halo candidates identify the same local solution across annual link
    blocks, but two blocks can choose incompatible earlier branches before their
    first shared observation.  Treating those branches as one event creates a
    missing hour or an impossible jump at 1 January.  The conservative response
    is to retain every published centre and start a new event identity at that
    discontinuity; no centre is interpolated or discarded here.
    """

    assigned = pd.Series(index=frame.index, dtype="int64")
    audit: list[dict[str, Any]] = []
    for root in sorted(set(roots.astype(str))):
        indexes = roots.index[roots.astype(str).eq(root)]
        indexes = timestamps.loc[indexes].sort_values(kind="mergesort").index
        active = int(active_ids[root])
        previous = last_positions.get(root)
        for index in indexes:
            timestamp = pd.Timestamp(timestamps.loc[index])
            lon = float(frame.at[index, "lon"])
            lat = float(frame.at[index, "lat"])
            if previous is not None:
                previous_time, previous_lon, previous_lat = previous
                elapsed_hours = float((timestamp - previous_time).total_seconds() / 3600.0)
                distance_km = _haversine_km(previous_lon, previous_lat, lon, lat)
                invalid_step = (
                    elapsed_hours != 1.0
                    or not np.isfinite(distance_km)
                    or distance_km > MAXIMUM_HOURLY_STEP_KM
                )
                if invalid_step:
                    old_id = active
                    active = next_track_id
                    next_track_id += 1
                    audit.append(
                        {
                            "reconciled_root": root,
                            "left_track_id": old_id,
                            "right_track_id": active,
                            "block_year": int(block_year),
                            "left_time": previous_time.isoformat(),
                            "right_time": timestamp.isoformat(),
                            "elapsed_hours": elapsed_hours,
                            "distance_km": distance_km if np.isfinite(distance_km) else None,
                            "reason": (
                                "non_hourly_boundary"
                                if elapsed_hours != 1.0
                                else "excessive_boundary_displacement"
                            ),
                        }
                    )
            assigned.at[index] = active
            previous = (timestamp, lon, lat)
        active_ids[root] = active
        if previous is not None:
            last_positions[root] = previous
    return assigned.astype(np.int64), next_track_id, audit


def merge(source: str, output_root: Path, run_root: Path) -> Path:
    source = validate_source_label(source)
    rows = completed_rows(run_root)
    identities, reconciliation = reconcile_blocks(rows)
    earliest: dict[str, pd.Timestamp] = {}
    for row in rows:
        year = int(row["core_year"])
        frame, timestamps = core_frame(row)
        roots = (f"{year}:" + frame["track_id"].astype(str)).map(identities.find)
        for root, timestamp in timestamps.groupby(roots).min().items():
            if root not in earliest or timestamp < earliest[root]:
                earliest[root] = timestamp
    ordered_roots = sorted(earliest, key=lambda root: (earliest[root], root))
    global_ids = {root: index for index, root in enumerate(ordered_roots)}
    active_ids = dict(global_ids)
    last_positions: dict[str, tuple[pd.Timestamp, float, float]] = {}
    next_track_id = len(global_ids)
    continuity_splits: list[dict[str, Any]] = []
    output_root = output_root.resolve()
    output = output_root / f"{source}-parallel-linked.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + f".part-{os.getpid()}")
    columns: list[str] | None = None
    observed_uids: set[str] = set()
    rows_written = observed_rows = interpolated_rows = 0
    minimum_time: pd.Timestamp | None = None
    maximum_time: pd.Timestamp | None = None
    for position, row in enumerate(rows):
        year = int(row["core_year"])
        frame, timestamps = core_frame(row)
        local_ids = frame["track_id"].astype(str)
        roots = (f"{year}:" + local_ids).map(identities.find)
        frame["parallel_block_year"] = year
        frame["parallel_local_track_id"] = local_ids
        assigned, next_track_id, split_audit = assign_continuous_track_ids(
            frame,
            timestamps,
            roots,
            active_ids,
            last_positions,
            next_track_id,
            block_year=year,
        )
        frame["track_id"] = assigned
        continuity_splits.extend(split_audit)
        if columns is None:
            columns = list(frame.columns)
        else:
            frame = frame.reindex(columns=columns)
        observed = frame["position_source"].astype(str).eq("observed")
        current_uids = set(frame.loc[observed, "candidate_uid"].dropna().astype(str))
        duplicated = observed_uids.intersection(current_uids)
        if duplicated:
            raise RuntimeError(f"duplicate observed candidates across core blocks: {sorted(duplicated)[:5]}")
        observed_uids.update(current_uids)
        rows_written += len(frame)
        observed_rows += int(observed.sum())
        interpolated_rows += int((~observed).sum())
        if len(timestamps):
            minimum_time = timestamps.min() if minimum_time is None else min(minimum_time, timestamps.min())
            maximum_time = timestamps.max() if maximum_time is None else max(maximum_time, timestamps.max())
        frame.to_csv(temporary, mode="w" if position == 0 else "a", header=position == 0, index=False)
    os.replace(temporary, output)
    input_manifest = json.loads((run_root / "input-manifest.json").read_text(encoding="utf-8"))
    summary = {
        "schema": SCHEMA,
        "status": "complete",
        "completed_utc": utc_now(),
        "source": source,
        "blocks": len(rows),
        "accepted_tracks": next_track_id,
        "accepted_output_rows": rows_written,
        "accepted_observed_rows": observed_rows,
        "posterior_rows": interpolated_rows,
        "coverage_start_utc": minimum_time.isoformat().replace("+00:00", "Z") if minimum_time is not None else None,
        "coverage_end_utc": maximum_time.isoformat().replace("+00:00", "Z") if maximum_time is not None else None,
        "reconciliation": reconciliation,
        "continuity": {
            "maximum_hourly_step_km": MAXIMUM_HOURLY_STEP_KM,
            "split_count": len(continuity_splits),
            "splits": continuity_splits,
            "method": (
                "Reconciled identities are split, without adding or removing centres, when "
                "successive retained core rows are not exactly hourly or move more than the "
                "final-catalogue displacement limit."
            ),
        },
        "linked": {"path": str(output), "bytes": output.stat().st_size, "sha256": sha256(output)},
        "input_manifest": {"path": str(run_root / "input-manifest.json"), "sha256": sha256(run_root / "input-manifest.json")},
        "candidate_months": input_manifest["candidate_months"],
        "parameters": input_manifest["parameters"],
        "linker": input_manifest["linker"],
    }
    atomic_json(output_root / f"{source}-parallel-link-summary.json", summary)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source", required=True)
    prepare_parser.add_argument("--output-root", type=Path, required=True)
    prepare_parser.add_argument("--run-root", type=Path, required=True)
    prepare_parser.add_argument("--force", action="store_true")
    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--run-root", type=Path, required=True)
    worker_parser.add_argument("--task-id", type=int, required=True)
    worker_parser.add_argument("--force", action="store_true")
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--source", required=True)
    merge_parser.add_argument("--output-root", type=Path, required=True)
    merge_parser.add_argument("--run-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        path = prepare(args.source, args.output_root, args.run_root, force=args.force)
    elif args.command == "worker":
        path = worker(args.run_root, args.task_id, force=args.force)
    else:
        path = merge(args.source, args.output_root, args.run_root)
    print(path)


if __name__ == "__main__":
    main()
