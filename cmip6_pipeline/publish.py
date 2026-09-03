#!/usr/bin/env python3
"""Create compact, source-native CMIP6 track assets and QA summaries."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reanalysis_pipeline.common import sha256
from reanalysis_pipeline.selection import select_physical_tracks


TRACK_COLUMNS = (
    "track_id",
    "time",
    "lon",
    "lat",
    "lon_detected",
    "lat_detected",
    "position_source",
    "life_stage",
    "imd_category_raw",
    "imd_label_raw",
    "max_vort_smoothed",
    "max_wind",
    "min_mslp",
    "pressure_deficit_hpa",
    "closed_isobars_2hpa_local",
    "precip_1hr",
    "precip_24hr",
    "q850_mean_gkg",
    "rh850_mean_pct",
    "t850_mean_k",
    "q700_mean_gkg",
    "rh700_mean_pct",
    "t700_mean_k",
    "q500_mean_gkg",
    "rh500_mean_pct",
    "t500_mean_k",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _track_summaries(frame: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for track_id, group in frame.groupby("track_id", sort=True):
        group = group.sort_values("time")
        observed = group.position_source.astype(str).str.lower().eq("observed")
        records.append(
            {
                "track_id": int(track_id),
                "start": group.time.min(),
                "end": group.time.max(),
                "duration_hours": (group.time.max() - group.time.min()).total_seconds() / 3600.0,
                "positions": len(group),
                "observed_positions": int(observed.sum()),
                "genesis_lon": float(group.lon.iloc[0]),
                "genesis_lat": float(group.lat.iloc[0]),
                "lysis_lon": float(group.lon.iloc[-1]),
                "lysis_lat": float(group.lat.iloc[-1]),
                "maximum_detector_category": int(pd.to_numeric(group.imd_category_raw, errors="coerce").max()),
                "maximum_wind_ms": float(pd.to_numeric(group.max_wind, errors="coerce").max()),
                "maximum_pressure_deficit_hpa": float(pd.to_numeric(group.pressure_deficit_hpa, errors="coerce").max()),
                "maximum_vorticity_x1e5_s1": float(pd.to_numeric(group.max_vort_smoothed, errors="coerce").max()),
                "maximum_24h_precipitation_mm": float(pd.to_numeric(group.precip_24hr, errors="coerce").max()),
            }
        )
    return pd.DataFrame.from_records(records)


def publish(
    linked: Path,
    output: Path,
    *,
    activity: str,
    institution: str,
    source_id: str,
    experiment_id: str,
    member_id: str,
    grid_label: str,
) -> Path:
    frame = pd.read_csv(linked)
    frame["time"] = pd.to_datetime(frame.time, utc=True).dt.tz_convert(None)
    selected, selection = select_physical_tracks(frame)
    missing = sorted(set(TRACK_COLUMNS) - set(selected.columns))
    if missing:
        raise ValueError(f"linked CMIP6 table lacks compact-publication columns: {missing}")
    tracks = selected.loc[:, TRACK_COLUMNS].copy()
    tracks.insert(0, "grid_label", grid_label)
    tracks.insert(0, "member_id", member_id)
    tracks.insert(0, "experiment_id", experiment_id)
    tracks.insert(0, "source_id", source_id)
    tracks.insert(0, "institution", institution)
    tracks.insert(0, "activity", activity)
    tracks.insert(
        6,
        "event_id",
        [f"{source_id}/{experiment_id}/{member_id}/{int(value)}" for value in tracks.track_id],
    )
    summaries = _track_summaries(tracks)
    summaries.insert(0, "grid_label", grid_label)
    summaries.insert(0, "member_id", member_id)
    summaries.insert(0, "experiment_id", experiment_id)
    summaries.insert(0, "source_id", source_id)
    summaries.insert(0, "institution", institution)
    summaries.insert(0, "activity", activity)
    summaries.insert(
        6,
        "event_id",
        [f"{source_id}/{experiment_id}/{member_id}/{int(value)}" for value in summaries.track_id],
    )

    track_path = output / "tracks.parquet"
    summary_path = output / "events.parquet"
    _atomic_parquet(track_path, tracks)
    _atomic_parquet(summary_path, summaries)
    maxima = (
        summaries.maximum_detector_category.value_counts().sort_index().astype(int).to_dict()
        if not summaries.empty
        else {}
    )
    manifest = {
        "schema": "lps-atlas-cmip6-track-asset-v1",
        "generated_utc": utc_now(),
        "run": {
            "activity": activity,
            "institution": institution,
            "source_id": source_id,
            "experiment_id": experiment_id,
            "member_id": member_id,
            "grid_label": grid_label,
        },
        "method": {
            "detector_linker": "Frozen v5.6 detector-space method on a shared 1-degree grid",
            "selection": selection,
            "intensity_status": (
                "Detector-centre provisional categories only. Final-centre resampling, a common land mask, "
                "and the v5.5.1 closed-isobar intensity pass remain to be added before atlas publication."
            ),
        },
        "coverage": {
            "start": tracks.time.min().isoformat() if not tracks.empty else None,
            "end": tracks.time.max().isoformat() if not tracks.empty else None,
        },
        "counts": {
            "linked_tracks": int(selection["linker_tracks"]),
            "selected_tracks": int(selection["selected_tracks"]),
            "selected_positions": int(len(tracks)),
            "maximum_detector_category": {str(key): value for key, value in maxima.items()},
        },
        "assets": {
            "tracks": {"path": str(track_path), "bytes": track_path.stat().st_size, "sha256": sha256(track_path)},
            "events": {"path": str(summary_path), "bytes": summary_path.stat().st_size, "sha256": sha256(summary_path)},
            "linked_source": {"path": str(linked), "bytes": linked.stat().st_size, "sha256": sha256(linked)},
        },
    }
    manifest_path = output / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linked", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activity", required=True)
    parser.add_argument("--institution", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--member-id", required=True)
    parser.add_argument("--grid-label", default="gn")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = publish(
        args.linked,
        args.output,
        activity=args.activity,
        institution=args.institution,
        source_id=args.source_id,
        experiment_id=args.experiment_id,
        member_id=args.member_id,
        grid_label=args.grid_label,
    )
    print(path)


if __name__ == "__main__":
    main()
