#!/usr/bin/env python3
"""Build lazy monthly source-native track assets for Forecast Archive view."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

from .common import sha256
from .selection import REQUIRED_COLUMNS, SELECTION_SCHEMA, THRESHOLDS, physical_track_passes
from .track import SOURCES


NATIVE_INDEX_SCHEMA = "lps-atlas-reanalysis-native-index-v1"
NATIVE_MONTH_SCHEMA = "lps-atlas-reanalysis-native-month-v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_gzip_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as stream:
            stream.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    os.replace(temporary, path)


def track_columns(path: Path) -> tuple[list[str], str, str]:
    columns = list(pd.read_csv(path, nrows=0).columns)
    required = {"track_id", "time"}
    if not required.issubset(columns):
        raise ValueError(f"{path} lacks {sorted(required - set(columns))}")
    longitude = next((name for name in ("lon_smooth", "lon", "longitude") if name in columns), None)
    latitude = next((name for name in ("lat_smooth", "lat", "latitude") if name in columns), None)
    if longitude is None or latitude is None:
        raise ValueError(f"{path} lacks track longitude/latitude")
    missing = sorted(REQUIRED_COLUMNS - set(columns))
    if missing:
        raise ValueError(f"{path} lacks physical-selection columns {missing}")
    selected = ["track_id", "time", longitude, latitude, *sorted(REQUIRED_COLUMNS - {"track_id", "time"})]
    for optional in ("position_source", "reject_reason"):
        if optional in columns:
            selected.append(optional)
    return selected, longitude, latitude


def compact_group(group: pd.DataFrame, longitude: str, latitude: str) -> tuple[list[list[Any]], set[str]]:
    times = pd.to_datetime(group["time"], utc=True, errors="coerce")
    lon = pd.to_numeric(group[longitude], errors="coerce")
    lat = pd.to_numeric(group[latitude], errors="coerce")
    valid = times.notna() & lon.notna() & lat.notna()
    if not bool(valid.all()):
        raise ValueError("source-native track contains invalid time or position")
    order = np.argsort(times.astype("int64").to_numpy(), kind="stable")
    epoch_hours = (times.astype("int64").to_numpy()[order] // 3_600_000_000_000).astype(np.int64)
    lon_values = lon.to_numpy(dtype=float)[order]
    lat_values = lat.to_numpy(dtype=float)[order]
    if "position_source" in group:
        positions = group["position_source"].astype(str).str.lower().to_numpy()[order]
    else:
        positions = np.full(len(group), "observed", dtype=object)
    points = [
        [int(hour), round(float(x), 3), round(float(y), 3), "o" if source == "observed" else "i"]
        for hour, x, y, source in zip(epoch_hours, lon_values, lat_values, positions, strict=True)
    ]
    months = set(times.dt.strftime("%Y%m"))
    return points, months


def iter_track_groups(path: Path, *, chunksize: int = 250_000) -> Iterator[pd.DataFrame]:
    selected, _, _ = track_columns(path)
    carry: pd.DataFrame | None = None
    seen: set[str] = set()
    for chunk in pd.read_csv(path, usecols=selected, chunksize=chunksize, low_memory=False):
        if "reject_reason" in chunk:
            chunk = chunk.loc[chunk["reject_reason"].astype(str).eq("accepted")]
        if chunk.empty:
            continue
        if carry is not None:
            chunk = pd.concat([carry, chunk], ignore_index=True)
        last_id = chunk["track_id"].iloc[-1]
        complete = chunk.loc[chunk["track_id"] != last_id]
        carry = chunk.loc[chunk["track_id"] == last_id].copy()
        for track_id, group in complete.groupby("track_id", sort=False):
            key = str(track_id)
            if key in seen:
                raise ValueError(f"linked CSV is not contiguous by track_id; repeated {key}")
            seen.add(key)
            yield group
    if carry is not None and not carry.empty:
        key = str(carry["track_id"].iloc[0])
        if key in seen:
            raise ValueError(f"linked CSV is not contiguous by track_id; repeated {key}")
        yield carry


def build_native_archive(source: str, linked_path: Path, output: Path, *, chunksize: int = 250_000) -> Path:
    if source not in SOURCES:
        raise ValueError(f"unsupported source {source}")
    linked_path = linked_path.resolve()
    output = output.resolve()
    _, longitude, latitude = track_columns(linked_path)
    tracks_by_month: dict[str, dict[str, list[list[Any]]]] = defaultdict(dict)
    first_hour: int | None = None
    last_hour: int | None = None
    first_track_hour: int | None = None
    last_track_hour: int | None = None
    track_count = 0
    point_count = 0
    linker_track_count = 0
    for group in iter_track_groups(linked_path, chunksize=chunksize):
        linker_track_count += 1
        source_times = pd.to_datetime(group["time"], utc=True, errors="coerce")
        if source_times.isna().any():
            raise ValueError("source-native linker table contains an invalid time")
        source_hours = source_times.astype("int64").to_numpy() // 3_600_000_000_000
        first_hour = int(source_hours.min()) if first_hour is None else min(first_hour, int(source_hours.min()))
        last_hour = int(source_hours.max()) if last_hour is None else max(last_hour, int(source_hours.max()))
        if not physical_track_passes(group):
            continue
        track_id = str(group["track_id"].iloc[0])
        points, months = compact_group(group, longitude, latitude)
        if not points:
            continue
        for month in months:
            tracks_by_month[month][track_id] = points
        first_track_hour = points[0][0] if first_track_hour is None else min(first_track_hour, points[0][0])
        last_track_hour = points[-1][0] if last_track_hour is None else max(last_track_hour, points[-1][0])
        track_count += 1
        point_count += len(points)
    if first_hour is None or last_hour is None or first_track_hour is None or last_track_hour is None:
        raise ValueError(f"{linked_path} has no accepted source-native tracks")

    first = pd.Timestamp(first_hour * 3600, unit="s", tz="UTC")
    last = pd.Timestamp(last_hour * 3600, unit="s", tz="UTC")
    first_track = pd.Timestamp(first_track_hour * 3600, unit="s", tz="UTC")
    last_track = pd.Timestamp(last_track_hour * 3600, unit="s", tz="UTC")
    months = [period.strftime("%Y%m") for period in pd.period_range(first.strftime("%Y-%m"), last.strftime("%Y-%m"), freq="M")]
    records: dict[str, dict[str, Any]] = {}
    generated = utc_now()
    for month in months:
        path = output / f"{month}.json.gz"
        tracks = tracks_by_month.get(month, {})
        atomic_gzip_json(path, {
            "schema": NATIVE_MONTH_SCHEMA,
            "source": source,
            "month": month,
            "generated_utc": generated,
            "tracks": tracks,
        })
        records[month] = {
            "url": f"{month}.json.gz",
            "tracks": len(tracks),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    index = {
        "schema": NATIVE_INDEX_SCHEMA,
        "source": source,
        "generated_utc": generated,
        "coverage_start_utc": first.isoformat().replace("+00:00", "Z"),
        "coverage_end_utc": last.isoformat().replace("+00:00", "Z"),
        "physical_track_start_utc": first_track.isoformat().replace("+00:00", "Z"),
        "physical_track_end_utc": last_track.isoformat().replace("+00:00", "Z"),
        "track_count": track_count,
        "linker_track_count": linker_track_count,
        "rejected_linker_track_count": linker_track_count - track_count,
        "point_count": point_count,
        "month_count": len(months),
        "months": records,
        "selection": {
            "schema": SELECTION_SCHEMA,
            "basis": "Frozen v5.6 detector-space physical-event thresholds",
            "thresholds": THRESHOLDS,
        },
        "method_note": "Source-native tracks that pass the frozen detector-space physical-event gate, independent of ERA5 identity matching. This does not assign ERA5-equivalent intensity or repeat ERA5-only final-centre physics. Each monthly file contains complete geometry for tracks active during that UTC month.",
    }
    index_path = output / "index.json"
    atomic_json(index_path, index)
    return index_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--linked", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunksize", type=int, default=250_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = build_native_archive(args.source, args.linked, args.output, chunksize=args.chunksize)
    print(path)


if __name__ == "__main__":
    main()
