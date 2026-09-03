#!/usr/bin/env python3
"""Final-centre physics, physical-event gating and v5.5.1 intensity for CMIP6."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import xarray as xr
from scipy.ndimage import label

from reanalysis_pipeline.common import sha256
from reanalysis_pipeline.standardise_merra2 import standard_paths


ATLAS_ROOT = Path(__file__).resolve().parents[1]
TRACKER_ROOT = ATLAS_ROOT.parent / "lps-v5.3-continuity-framework"
RECOMPUTE = TRACKER_ROOT / "lps52_recompute_smoothed.py"
PARAMETERS = TRACKER_ROOT / "params/lps_v5.4_domain_only.json"
GAP_VALIDATOR = TRACKER_ROOT / "production/v5.4.2/validate_and_split_physics_gaps.py"
EVENT_FILTER = TRACKER_ROOT / "production/v5.5/filter_calibrated_events.py"
SCHEMA = "lps-atlas-cmip6-final-physics-v1"
GAP_COMPLETENESS_COLUMN = "cmip6_gap_physics_complete"
EARTH_RADIUS_KM = 6371.0088
METRIC_RADIUS_KM = 125.0
BACKGROUND_INNER_KM = 300.0
BACKGROUND_OUTER_KM = 500.0
ISOBAR_INTERVAL_HPA = 2.0
ISOBAR_WINDOW_DEG = 10.0
ISOBAR_MAXIMUM_CONTOURS = 8
CONNECTIVITY = np.ones((3, 3), dtype=np.int8)
PERSISTENCE_HOURS = 6
LAND_FRACTION_MINIMUM = 0.95
LAND_DEPRESSION_CLOSED_ISOBARS = 2
LAND_DEPRESSION_CIRCULATION_MS = 17.0 * 0.514444
THRESHOLDS_MS = {
    "depression": 10.864504601961277,
    "deep_depression": 14.212801616020542,
    "cyclonic_storm": 18.83486906837853,
    "severe_cyclonic_storm": 26.590403390652043,
    "very_severe_or_extremely_severe": 35.45387118753606,
    "super_cyclonic_storm": 66.47600847663011,
}
GRADE_LABELS = {
    0: "unclassified",
    1: "low",
    2: "depression",
    3: "deep_depression",
    4: "cyclonic_storm",
    5: "severe_cyclonic_storm",
    6: "very_severe_to_extremely_severe_cyclonic_storm",
    7: "super_cyclonic_storm",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def require_complete_gap_blocks(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Reject a whole interpolated bridge when any required field is unavailable.

    Pressure-level variables are legitimately missing below CMIP6 model topography.
    The upstream gap validator permits a small number of unsupported hours within
    an otherwise physical bridge, but its release invariant still requires every
    retained bridge row to have complete final-centre physics. Propagating an
    incomplete flag across its contiguous bridge makes that invariant explicit:
    the validator removes the bridge and preserves the observed pieces as separate
    events rather than publishing an unverifiable interpolation over high terrain.
    """
    required = {"track_id", "time", "position_source", "physics_complete_v54rean"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"gap-completeness input lacks {missing}")
    output = frame.copy()
    ordered = output.sort_values(["track_id", "time"], kind="mergesort")
    observed = ordered.position_source.astype(str).str.lower().eq("observed")
    complete = ordered.physics_complete_v54rean.fillna(False).astype(bool)
    run_start = ordered.track_id.ne(ordered.track_id.shift()) | observed.ne(observed.shift())
    run_id = run_start.cumsum()
    gap = ~observed
    block_complete = complete.groupby(run_id, sort=False).transform("all")
    validation_complete = complete.copy()
    validation_complete.loc[gap] = block_complete.loc[gap]
    output.loc[ordered.index, GAP_COMPLETENESS_COLUMN] = validation_complete.to_numpy(bool)

    gap_blocks = pd.DataFrame(
        {
            "run_id": run_id.loc[gap].to_numpy(),
            "rows": np.ones(int(gap.sum()), dtype=np.int64),
            "complete": complete.loc[gap].to_numpy(bool),
        }
    )
    if gap_blocks.empty:
        block_summary = pd.DataFrame(columns=["rows", "complete"])
    else:
        block_summary = gap_blocks.groupby("run_id", sort=False).agg(
            rows=("rows", "sum"), complete=("complete", "all")
        )
    rejected = block_summary.loc[~block_summary.complete.astype(bool)]
    summary = {
        "schema": "lps-atlas-cmip6-gap-completeness-v1",
        "status": "complete",
        "method": "reject_contiguous_interpolated_bridge_if_any_required_field_is_unavailable",
        "rows": int(len(output)),
        "gap_rows": int(gap.sum()),
        "incomplete_gap_rows": int((gap & ~complete).sum()),
        "gap_blocks": int(len(block_summary)),
        "incomplete_gap_blocks_rejected": int(len(rejected)),
        "gap_rows_in_rejected_blocks": int(rejected.rows.sum()) if len(rejected) else 0,
    }
    return output, summary


def drop_unobserved_track_fragments(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Remove fragments made solely of posterior positions after a hard split.

    The physical-event filter requires at least one actual detection per event.
    A continuity split can isolate one or more interpolated rows around an
    impossible linker jump; those rows are neither a detected event nor a safe
    bridge, so retaining them would manufacture an observation-free system.
    """

    required = {"track_id", "position_source"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"observed-fragment input lacks {missing}")
    observed = frame.position_source.astype(str).str.lower().eq("observed")
    supported = observed.groupby(frame.track_id, sort=False).transform("any")
    removed = frame.loc[~supported]
    output = frame.loc[supported].copy()
    return output, {
        "unobserved_fragments_removed": int(removed.track_id.nunique()),
        "unobserved_rows_removed": int(len(removed)),
    }


def prepare_gap_validation_input(initial: Path, staging: Path) -> tuple[Path, str, Path]:
    frame = read_table(initial)
    frame, fragment_summary = drop_unobserved_track_fragments(frame)
    prepared, summary = require_complete_gap_blocks(frame)
    path = staging / "gap-validation-input.parquet"
    summary_path = staging / "gap-completeness-summary.json"
    atomic_parquet(path, prepared)
    summary.update(
        {
            "created_utc": utc_now(),
            "input": {"path": str(initial), "sha256": sha256(initial)},
            "prepared_input": {"sha256": sha256(path), "temporary": True},
            **fragment_summary,
        }
    )
    atomic_json(summary_path, summary)
    return path, GAP_COMPLETENESS_COLUMN, summary_path


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path, low_memory=False)


def prepare(linked: Path, output_root: Path, source_label: str, *, force: bool = False) -> Path:
    linked = linked.resolve()
    output_root = output_root.resolve()
    manifest_path = output_root / "monthly-input-manifest.csv"
    metadata_path = output_root / "prepare-summary.json"
    fingerprint = {"linked": str(linked), "linked_sha256": sha256(linked), "source_label": source_label}
    if manifest_path.is_file() and metadata_path.is_file() and not force:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("fingerprint") == fingerprint:
            return manifest_path
        raise FileExistsError(f"physics plan differs from {metadata_path}")

    frame = read_table(linked)
    required = {"track_id", "time", "lon", "lat", "position_source", "candidate_uid"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"linked table lacks {missing}")
    frame["time"] = pd.to_datetime(frame["time"], errors="raise")
    frame.sort_values(["track_id", "time"], kind="mergesort", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    if frame.duplicated(["track_id", "time"]).any():
        raise ValueError("linked table has duplicate track/time rows")
    if "row_id" in frame:
        if "detector_row_id" in frame:
            raise ValueError("linked table already contains both row_id and detector_row_id")
        frame.rename(columns={"row_id": "detector_row_id"}, inplace=True)
    frame.insert(0, "row_id", np.arange(len(frame), dtype=np.int64))
    frame["cmip6_source_label"] = source_label
    frame["yyyymm"] = frame["time"].dt.strftime("%Y%m")
    whole = output_root / "linked-with-row-ids.parquet"
    atomic_parquet(whole, frame.drop(columns="yyyymm"))

    records: list[dict[str, Any]] = []
    for task_id, (yyyymm, month) in enumerate(frame.groupby("yyyymm", sort=True)):
        path = output_root / "monthly-input" / f"physics-input-{yyyymm}.parquet"
        atomic_parquet(path, month.drop(columns="yyyymm"))
        records.append(
            {
                "task_id": task_id,
                "yyyymm": yyyymm,
                "path": str(path),
                "rows": len(month),
                "sha256": sha256(path),
            }
        )
    manifest = pd.DataFrame.from_records(records)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(f".csv.part-{os.getpid()}")
    manifest.to_csv(temporary, index=False)
    os.replace(temporary, manifest_path)
    atomic_json(
        metadata_path,
        {
            "schema": SCHEMA,
            "status": "prepared",
            "created_utc": utc_now(),
            "fingerprint": fingerprint,
            "rows": len(frame),
            "tracks": int(frame.track_id.nunique()),
            "months": len(manifest),
            "coverage_start": frame.time.min().isoformat(),
            "coverage_end": frame.time.max().isoformat(),
            "manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
            "row_identity": {"path": str(whole), "sha256": sha256(whole)},
        },
    )
    return manifest_path


def distance_grid(lon: np.ndarray, lat: np.ndarray, centre_lon: float, centre_lat: float) -> np.ndarray:
    phi1 = np.deg2rad(centre_lat)
    phi2 = np.deg2rad(lat)
    dphi = phi2 - phi1
    dlon = np.deg2rad(lon - centre_lon)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlon / 2.0) ** 2
    return EARTH_RADIUS_KM * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def count_closed_isobars(
    pressure_hpa: np.ndarray,
    minimum_i: int,
    minimum_j: int,
    *,
    interval_hpa: float = ISOBAR_INTERVAL_HPA,
    maximum_contours: int = ISOBAR_MAXIMUM_CONTOURS,
) -> tuple[int, float, int]:
    field = np.asarray(pressure_hpa, dtype=float)
    minimum = float(field[minimum_i, minimum_j])
    if not np.isfinite(minimum):
        return 0, np.nan, 0
    first = float((np.floor(minimum / interval_hpa) + 1.0) * interval_hpa)
    count, outermost_level, outermost_size = 0, np.nan, 0
    for level_hpa in first + interval_hpa * np.arange(maximum_contours):
        components, _ = label(np.isfinite(field) & (field <= level_hpa), structure=CONNECTIVITY)
        component = int(components[minimum_i, minimum_j])
        if component == 0:
            break
        points = np.argwhere(components == component)
        if np.any(
            (points[:, 0] == 0)
            | (points[:, 0] == field.shape[0] - 1)
            | (points[:, 1] == 0)
            | (points[:, 1] == field.shape[1] - 1)
        ):
            break
        count = count + 1
        outermost_level = float(level_hpa)
        outermost_size = int(len(points))
    return count, outermost_level, outermost_size


def add_surface_metrics(frame: pd.DataFrame, surface_file: Path) -> pd.DataFrame:
    output = frame.copy()
    output["time"] = pd.to_datetime(output.time, errors="raise")
    with xr.open_dataset(surface_file) as dataset:
        times = pd.DatetimeIndex(pd.to_datetime(dataset.time.values))
        contour_latitude = np.asarray(dataset.latitude.values, dtype=float)
        contour_longitude = np.asarray(dataset.longitude.values, dtype=float)
        pressure = np.asarray(dataset.msl.values, dtype=float) / 100.0
        # The 125-km P95 calibration was defined on a 0.25-degree ERA5 grid.
        # Bilinearly oversample the common 1-degree wind field before applying
        # that sampling operator; this adds no model information, but avoids a
        # centre-dependent 1--3 point percentile on the coarse grid.
        wind_latitude = np.arange(contour_latitude.min(), contour_latitude.max() + 0.01, 0.25)
        wind_longitude = np.arange(contour_longitude.min(), contour_longitude.max() + 0.01, 0.25)
        wind = dataset[["u10", "v10"]].interp(
            latitude=xr.DataArray(wind_latitude, dims="latitude"),
            longitude=xr.DataArray(wind_longitude, dims="longitude"),
            method="linear",
        ).load()
    u_all = np.asarray(wind.u10.values, dtype=float)
    v_all = np.asarray(wind.v10.values, dtype=float)
    time_index = {pd.Timestamp(value): index for index, value in enumerate(times)}

    metric = np.full(len(output), np.nan, dtype=np.float32)
    background_u = np.full(len(output), np.nan, dtype=np.float32)
    background_v = np.full(len(output), np.nan, dtype=np.float32)
    core_points = np.zeros(len(output), dtype=np.int16)
    background_points = np.zeros(len(output), dtype=np.int16)
    contour_count = np.zeros(len(output), dtype=np.int8)
    contour_level = np.full(len(output), np.nan, dtype=np.float32)
    contour_size = np.zeros(len(output), dtype=np.int32)
    contour_minimum = np.full(len(output), np.nan, dtype=np.float32)

    for position, row in enumerate(output.itertuples(index=False)):
        index = time_index.get(pd.Timestamp(row.time))
        if index is None:
            raise KeyError(f"{row.time} absent from {surface_file}")
        lat_pad = 4.7
        lon_pad = 4.7 / max(np.cos(np.deg2rad(float(row.lat))), 0.35)
        lat_index = np.flatnonzero(np.abs(wind_latitude - float(row.lat)) <= lat_pad)
        lon_index = np.flatnonzero(np.abs(wind_longitude - float(row.lon)) <= lon_pad)
        lon_grid, lat_grid = np.meshgrid(wind_longitude[lon_index], wind_latitude[lat_index])
        distance = distance_grid(lon_grid, lat_grid, float(row.lon), float(row.lat))
        local_u = u_all[index][np.ix_(lat_index, lon_index)]
        local_v = v_all[index][np.ix_(lat_index, lon_index)]
        background = (
            (distance >= BACKGROUND_INNER_KM)
            & (distance <= BACKGROUND_OUTER_KM)
            & np.isfinite(local_u)
            & np.isfinite(local_v)
        )
        if int(background.sum()) < 4:
            raise RuntimeError(f"insufficient wind-background points for row_id={row.row_id}")
        bu = float(np.mean(local_u[background]))
        bv = float(np.mean(local_v[background]))
        anomaly = np.hypot(local_u - bu, local_v - bv)
        core = (distance <= METRIC_RADIUS_KM) & np.isfinite(anomaly)
        if int(core.sum()) < 4:
            raise RuntimeError(f"insufficient wind-core points for row_id={row.row_id}")
        metric[position] = np.percentile(anomaly[core], 95)
        background_u[position], background_v[position] = bu, bv
        core_points[position], background_points[position] = int(core.sum()), int(background.sum())

        minimum_lat = float(row.mslp_min_lat)
        minimum_lon = float(row.mslp_min_lon)
        if not (np.isfinite(minimum_lat) and np.isfinite(minimum_lon)):
            continue
        centre_i = int(np.argmin(np.abs(contour_latitude - minimum_lat)))
        centre_j = int(np.argmin(np.abs(contour_longitude - minimum_lon)))
        contour_lon_pad = ISOBAR_WINDOW_DEG / max(np.cos(np.deg2rad(contour_latitude[centre_i])), 0.35)
        contour_lat_index = np.flatnonzero(
            np.abs(contour_latitude - contour_latitude[centre_i]) <= ISOBAR_WINDOW_DEG
        )
        contour_lon_index = np.flatnonzero(
            np.abs(contour_longitude - contour_longitude[centre_j]) <= contour_lon_pad
        )
        field = pressure[index][np.ix_(contour_lat_index, contour_lon_index)]
        local_i = int(np.flatnonzero(contour_lat_index == centre_i)[0])
        local_j = int(np.flatnonzero(contour_lon_index == centre_j)[0])
        count, level_hpa, size = count_closed_isobars(field, local_i, local_j)
        contour_count[position], contour_level[position], contour_size[position] = count, level_hpa, size
        contour_minimum[position] = field[local_i, local_j]

    output["p95_anomaly_wind_125km_ms"] = metric
    output["background_u_300_500km_ms"] = background_u
    output["background_v_300_500km_ms"] = background_v
    output["wind_metric_core_finite_points"] = core_points
    output["wind_metric_background_finite_points"] = background_points
    output["wind_metric_source"] = (
        "cmip6_10m_common_1deg_bilinearly_sampled_0p25deg_background_removed_at_published_centre_v55"
    )
    output["closed_isobars_2hpa_actual"] = contour_count
    output["closed_isobar_outermost_level_hpa"] = contour_level
    output["closed_isobar_outermost_component_gridcells"] = contour_size
    output["closed_isobar_local_minimum_hpa"] = contour_minimum
    output["closed_isobar_analysis_window_lat_deg"] = ISOBAR_WINDOW_DEG
    output["closed_isobar_method"] = "v551_standard_2hpa_connected_component_common_1deg_v1"
    return output


def _manifest_row(manifest: Path, task_id: int) -> pd.Series:
    table = pd.read_csv(manifest, dtype={"yyyymm": str})
    if task_id < 0 or task_id >= len(table):
        raise IndexError(f"task {task_id} outside 0..{len(table) - 1}")
    return table.iloc[task_id]


def worker(
    manifest: Path,
    task_id: int,
    data_root: Path,
    output_root: Path,
    static_file: Path,
) -> Path:
    manifest = manifest.resolve()
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    static_file = static_file.resolve()
    row = _manifest_row(manifest, task_id)
    yyyymm = str(row.yyyymm)
    source = Path(row.path)
    paths = standard_paths(data_root, yyyymm)
    month_period = pd.Period(yyyymm, freq="M")
    previous_paths = standard_paths(data_root, (month_period - 1).strftime("%Y%m"))
    next_paths = standard_paths(data_root, (month_period + 1).strftime("%Y%m"))
    output = output_root / "monthly-physics" / f"physics-{yyyymm}.parquet"
    status_path = output_root / "monthly-physics" / f"physics-{yyyymm}.status.json"
    fingerprint = {
        "schema": SCHEMA,
        "source_sha256": str(row.sha256),
        "vorticity_sha256": sha256(paths["vorticity"]),
        "surface_sha256": sha256(paths["surface"]),
        "precipitation_sha256": sha256(paths["precipitation"]),
        "previous_precipitation_sha256": sha256(previous_paths["precipitation"]),
        "auxiliary_sha256": sha256(paths["auxiliary"]),
        "next_auxiliary_sha256": sha256(next_paths["auxiliary"]),
        "static_sha256": sha256(static_file),
        "physics_module_sha256": sha256(Path(__file__).resolve()),
        "recompute_sha256": sha256(RECOMPUTE),
        "parameters_sha256": sha256(PARAMETERS),
    }
    if status_path.is_file() and output.is_file():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if (
            status.get("status") == "complete"
            and status.get("fingerprint") == fingerprint
            and status.get("output_sha256") == sha256(output)
        ):
            return output

    output.parent.mkdir(parents=True, exist_ok=True)
    working = output_root / "monthly-working" / f"recomputed-{yyyymm}.parquet"
    working.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(RECOMPUTE),
        str(source),
        "--out",
        str(working),
        "--params",
        str(PARAMETERS),
        "--vort-dir",
        str(data_root / "standard/vorticity"),
        "--precip-dir",
        str(data_root / "standard/precipitation"),
        "--sfc-dir",
        str(data_root / "standard/surface"),
        "--aux-dir",
        str(data_root / "standard/auxiliary"),
        "--static-file",
        str(static_file),
        "--suffix",
        "_v54rean",
        "--replace-main",
        "--quiet",
    ]
    started = time.monotonic()
    atomic_json(status_path, {"status": "running", "started_utc": utc_now(), "fingerprint": fingerprint})
    try:
        subprocess.run(command, cwd=TRACKER_ROOT, check=True)
        frame = pd.read_parquet(working)
        frame = add_surface_metrics(frame, paths["surface"])
        frame["physics_source"] = "cmip6_common_1deg_resampled_at_published_centre"
        if "physics_source_v54rean" in frame:
            frame["physics_source_v54rean"] = frame["physics_source"]
        atomic_parquet(output, frame)
    except Exception as error:
        atomic_json(
            status_path,
            {
                "status": "failed",
                "updated_utc": utc_now(),
                "fingerprint": fingerprint,
                "command": command,
                "error": repr(error),
            },
        )
        raise
    record = {
        "status": "complete",
        "completed_utc": utc_now(),
        "elapsed_seconds": time.monotonic() - started,
        "yyyymm": yyyymm,
        "rows": len(frame),
        "fingerprint": fingerprint,
        "command": command,
        "output": str(output),
        "output_sha256": sha256(output),
    }
    atomic_json(status_path, record)
    return output


def merge_months(manifest: Path, output_root: Path) -> Path:
    table = pd.read_csv(manifest, dtype={"yyyymm": str})
    parts: list[pd.DataFrame] = []
    for row in table.itertuples(index=False):
        path = output_root / "monthly-physics" / f"physics-{row.yyyymm}.parquet"
        status_path = output_root / "monthly-physics" / f"physics-{row.yyyymm}.status.json"
        if not path.is_file() or not status_path.is_file():
            raise FileNotFoundError(f"missing physics result for {row.yyyymm}")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "complete" or status.get("output_sha256") != sha256(path):
            raise RuntimeError(f"invalid physics result for {row.yyyymm}")
        parts.append(pd.read_parquet(path))
    frame = pd.concat(parts, ignore_index=True, sort=False)
    frame["time"] = pd.to_datetime(frame.time, errors="raise")
    frame.sort_values(["track_id", "time"], inplace=True)
    if frame.row_id.duplicated().any() or len(frame) != int(table.rows.sum()):
        raise RuntimeError("monthly physics merge does not preserve row identity")
    output = output_root / "physics-all-linked.parquet"
    atomic_parquet(output, frame)
    atomic_json(
        output_root / "physics-merge-summary.json",
        {
            "schema": SCHEMA,
            "status": "complete",
            "created_utc": utc_now(),
            "months": len(table),
            "rows": len(frame),
            "tracks": int(frame.track_id.nunique()),
            "output": {"path": str(output), "sha256": sha256(output)},
        },
    )
    return output


def persistent_category(
    raw: Sequence[Any],
    hours: int,
    frames: Sequence[Any],
    *,
    nominal_cadence_hours: float = 1.0,
) -> np.ndarray:
    raw_array = pd.to_numeric(pd.Series(raw), errors="coerce").fillna(0).to_numpy(dtype=int)
    frame_array = pd.to_numeric(pd.Series(frames), errors="coerce").to_numpy(float)
    if len(frame_array) != len(raw_array) or not np.isfinite(frame_array).all():
        raise ValueError("persistent-category clock is invalid")
    output = raw_array.copy()
    for category in range(7, 1, -1):
        mask = raw_array >= category
        start: int | None = None
        for index in range(len(mask) + 1):
            value = bool(mask[index]) if index < len(mask) else False
            continuation = (
                value
                and start is not None
                and index > start
                and frame_array[index] - frame_array[index - 1] <= 1.5 * nominal_cadence_hours
            )
            if value and start is None:
                start = index
            elif value and not continuation:
                support = frame_array[index - 1] - frame_array[start] + nominal_cadence_hours
                if support < hours:
                    output[start:index] = np.minimum(output[start:index], category - 1)
                start = index
            elif not value and start is not None:
                support = frame_array[index - 1] - frame_array[start] + nominal_cadence_hours
                if support < hours:
                    output[start:index] = np.minimum(output[start:index], category - 1)
                start = None
    return output


def classify_intensity(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = frame.copy()
    output["time"] = pd.to_datetime(output.time, errors="raise")
    output.sort_values(["track_id", "time"], inplace=True)
    output.reset_index(drop=True, inplace=True)
    values = pd.to_numeric(output.p95_anomaly_wind_125km_ms, errors="coerce").to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("circulation-wind metric is incomplete")
    thresholds = np.asarray(list(THRESHOLDS_MS.values()), dtype=float)
    wind_raw = np.digitize(values, thresholds, right=False).astype(np.int8) + 1
    pressure_support = (
        pd.to_numeric(output.land_fraction, errors="coerce").ge(LAND_FRACTION_MINIMUM)
        & pd.to_numeric(output.closed_isobars_2hpa_actual, errors="coerce").ge(LAND_DEPRESSION_CLOSED_ISOBARS)
        & pd.Series(values, index=output.index).ge(LAND_DEPRESSION_CIRCULATION_MS)
    )
    combined_raw = np.maximum(wind_raw, np.where(pressure_support, 2, 1).astype(np.int8))
    output["imd_category_wind_raw"] = wind_raw
    output["land_pressure_depression_support_raw"] = pressure_support
    output["imd_category_pressure_raw"] = np.where(pressure_support, 2, 1).astype(np.int8)
    output["imd_category_raw"] = combined_raw
    output["imd_label_raw"] = output.imd_category_raw.map(GRADE_LABELS)
    output["imd_category_wind_persistent"] = np.int8(0)
    output["imd_category"] = np.int8(0)
    peaks: dict[int, int] = {}
    wind_peaks: dict[int, int] = {}
    bases: dict[int, str] = {}
    for track_id, group in output.groupby("track_id", sort=False):
        clock = group.time.astype("int64").to_numpy(float) / 3.6e12
        if len(clock) > 1 and not np.allclose(np.diff(clock), 1.0):
            raise ValueError(f"track {track_id} is not hourly complete")
        wind = persistent_category(group.imd_category_wind_raw, PERSISTENCE_HOURS, clock).astype(np.int8)
        combined = persistent_category(group.imd_category_raw, PERSISTENCE_HOURS, clock).astype(np.int8)
        output.loc[group.index, "imd_category_wind_persistent"] = wind
        output.loc[group.index, "imd_category"] = combined
        wind_peaks[int(track_id)] = int(wind.max())
        peaks[int(track_id)] = int(combined.max())
        if peaks[int(track_id)] > wind_peaks[int(track_id)]:
            bases[int(track_id)] = "land_closed_isobar_assist"
        elif peaks[int(track_id)] >= 2:
            bases[int(track_id)] = "circulation_wind"
        else:
            bases[int(track_id)] = "below_depression_threshold"
    output["imd_label"] = output.imd_category.map(GRADE_LABELS)
    output["intensity_basis"] = np.where(
        output.imd_category.gt(output.imd_category_wind_persistent),
        "land_closed_isobar_assist",
        np.where(output.imd_category.ge(2), "circulation_wind", "below_depression_threshold"),
    )
    output["event_peak_imd_category"] = output.track_id.map(peaks).astype(np.int8)
    output["event_peak_imd_label"] = output.event_peak_imd_category.map(GRADE_LABELS)
    output["event_peak_intensity_basis"] = output.track_id.map(bases)
    output["intensity_metric"] = "p95_anomaly_wind_125km_ms"
    output["intensity_method"] = "v56_v551_hourly_wind_plus_land_closed_isobar_d_floor_common_1deg"
    output["intensity_persistence_hours"] = PERSISTENCE_HOURS
    output["intensity_land_fraction_minimum"] = LAND_FRACTION_MINIMUM
    output["intensity_land_depression_closed_isobars"] = LAND_DEPRESSION_CLOSED_ISOBARS
    output["intensity_land_depression_circulation_ms"] = LAND_DEPRESSION_CIRCULATION_MS
    counts = pd.Series(peaks).value_counts().sort_index()
    summary = {
        "rows": len(output),
        "events": int(output.track_id.nunique()),
        "event_peak_categories": {str(key): int(value) for key, value in counts.items()},
        "pressure_assisted_events": sum(peaks[key] > wind_peaks[key] for key in peaks),
        "thresholds_ms": THRESHOLDS_MS,
    }
    return output, summary


def finalize(initial: Path, output_root: Path) -> Path:
    initial = initial.resolve()
    output_root = output_root.resolve()
    final = output_root / "cmip6-physical-events.parquet"
    summary_path = output_root / "final-summary.json"
    fingerprint = {
        "input_sha256": sha256(initial),
        "physics_module_sha256": sha256(Path(__file__).resolve()),
        "gap_validator_sha256": sha256(GAP_VALIDATOR),
        "event_filter_sha256": sha256(EVENT_FILTER),
    }
    if final.is_file() and summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("fingerprint") == fingerprint and summary.get("output_sha256") == sha256(final):
            return final
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f"finalize-staging-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    gap_input, gap_completeness_column, gap_completeness_summary = (
        prepare_gap_validation_input(initial, staging)
    )
    gap = staging / "physics-gap-validated.parquet"
    gap_audit = staging / "physics-gap-audit.csv"
    gap_summary = staging / "physics-gap-summary.json"
    filtered = staging / "physical-events-unclassified.parquet"
    selection = staging / "physical-event-selection.parquet"
    selection_summary = staging / "physical-event-selection-summary.json"
    validation_environment = os.environ.copy()
    validation_environment["PYTHONWARNINGS"] = (
        "ignore:All-NaN slice encountered:RuntimeWarning"
    )
    subprocess.run(
        [
            sys.executable,
            str(GAP_VALIDATOR),
            "--input",
            str(gap_input),
            "--output",
            str(gap),
            "--audit",
            str(gap_audit),
            "--summary",
            str(gap_summary),
            "--minimum-supported-fraction",
            "0.65",
            "--short-gap-automatic-hours",
            "6",
            "--maximum-short-endpoint-speed-ms",
            "20",
            "--long-gap-hours",
            "12",
            "--long-gap-minimum-supported-fraction",
            "0.75",
            "--maximum-unsupported-run-hours",
            "4",
            "--minimum-vorticity",
            "5",
            "--minimum-pressure-deficit-hpa",
            "4",
            "--physics-complete-column",
            gap_completeness_column,
            "--require-extremum-proximity",
            "--maximum-vorticity-distance-km",
            "225",
            "--maximum-pressure-distance-km",
            "250",
        ],
        cwd=TRACKER_ROOT,
        env=validation_environment,
        check=True,
    )
    gap_input.unlink()
    subprocess.run(
        [
            sys.executable,
            str(EVENT_FILTER),
            "--input",
            str(gap),
            "--output",
            str(filtered),
            "--track-summary",
            str(selection),
            "--summary",
            str(selection_summary),
        ],
        cwd=TRACKER_ROOT,
        check=True,
    )
    classified, classification = classify_intensity(pd.read_parquet(filtered))
    classified.drop(columns=[GAP_COMPLETENESS_COLUMN], errors="ignore", inplace=True)
    atomic_parquet(final, classified)
    for source, name in (
        (gap, "physics-gap-validated.parquet"),
        (gap_audit, "physics-gap-audit.csv"),
        (gap_summary, "physics-gap-summary.json"),
        (filtered, "physical-events-unclassified.parquet"),
        (selection, "physical-event-selection.parquet"),
        (selection_summary, "physical-event-selection-summary.json"),
        (gap_completeness_summary, "gap-completeness-summary.json"),
    ):
        os.replace(source, output_root / name)
    atomic_json(
        summary_path,
        {
            "schema": SCHEMA,
            "status": "complete",
            "completed_utc": utc_now(),
            "fingerprint": fingerprint,
            "classification": classification,
            "output": str(final),
            "output_sha256": sha256(final),
            "method_note": (
                "Frozen v5.6 final-centre physical-event gate and v5.5.1 intensity thresholds; "
                "all model fields are sampled on the common 1-degree grid and land geography is fixed to ERA5."
            ),
        },
    )
    return final


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preparation = subparsers.add_parser("prepare")
    preparation.add_argument("--linked", type=Path, required=True)
    preparation.add_argument("--output-root", type=Path, required=True)
    preparation.add_argument("--source-label", required=True)
    preparation.add_argument("--force", action="store_true")
    monthly = subparsers.add_parser("worker")
    monthly.add_argument("--manifest", type=Path, required=True)
    monthly.add_argument("--task-id", type=int, required=True)
    monthly.add_argument("--data-root", type=Path, required=True)
    monthly.add_argument("--output-root", type=Path, required=True)
    monthly.add_argument("--static-file", type=Path, required=True)
    merger = subparsers.add_parser("merge")
    merger.add_argument("--manifest", type=Path, required=True)
    merger.add_argument("--output-root", type=Path, required=True)
    finalizer = subparsers.add_parser("finalize")
    finalizer.add_argument("--input", type=Path, required=True)
    finalizer.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        result = prepare(args.linked, args.output_root, args.source_label, force=args.force)
    elif args.command == "worker":
        result = worker(args.manifest, args.task_id, args.data_root, args.output_root, args.static_file)
    elif args.command == "merge":
        result = merge_months(args.manifest, args.output_root)
    else:
        result = finalize(args.input, args.output_root)
    print(result)


if __name__ == "__main__":
    main()
