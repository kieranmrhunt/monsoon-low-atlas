#!/usr/bin/env python3
"""Objectively match alternative-reanalysis tracks to ERA5 v5.6 events."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MATCH_SCHEMA = "lps-atlas-reanalysis-matches-v1"
MINIMUM_OVERLAP_HOURS = 12
MAXIMUM_MEDIAN_DISTANCE_KM = 300.0
MAXIMUM_P90_DISTANCE_KM = 500.0
MINIMUM_CLOSE_FRACTION = 0.50


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def haversine_km(lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray) -> np.ndarray:
    phi1 = np.radians(np.asarray(lat1, dtype=float))
    phi2 = np.radians(np.asarray(lat2, dtype=float))
    delta_phi = phi2 - phi1
    delta_lon = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    value = np.sin(delta_phi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(delta_lon / 2.0) ** 2
    return 12_742.0 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))


def normalise_tracks(frame: pd.DataFrame, *, source: str) -> pd.DataFrame:
    required = {"track_id", "time"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{source} tracks lack {sorted(required - set(frame.columns))}")
    longitude = next((name for name in ("lon_smooth", "lon", "longitude") if name in frame), None)
    latitude = next((name for name in ("lat_smooth", "lat", "latitude") if name in frame), None)
    if longitude is None or latitude is None:
        raise ValueError(f"{source} tracks lack longitude/latitude columns")
    output = frame.copy()
    output["time"] = pd.to_datetime(output["time"], utc=True).dt.tz_convert(None)
    output["longitude"] = pd.to_numeric(output[longitude], errors="coerce")
    output["latitude"] = pd.to_numeric(output[latitude], errors="coerce")
    output = output.dropna(subset=["time", "longitude", "latitude"])
    return output.sort_values(["track_id", "time"], kind="stable")


def track_pair_metrics(
    alternative: pd.DataFrame,
    era_track: pd.DataFrame,
    era_track_id: Any,
) -> dict[str, Any] | None:
    merged = alternative[["time", "longitude", "latitude"]].merge(
        era_track[["time", "longitude", "latitude"]],
        on="time",
        suffixes=("_source", "_era5"),
    )
    if len(merged) < MINIMUM_OVERLAP_HOURS:
        return None
    distances = haversine_km(
        merged["longitude_source"].to_numpy(),
        merged["latitude_source"].to_numpy(),
        merged["longitude_era5"].to_numpy(),
        merged["latitude_era5"].to_numpy(),
    )
    median = float(np.median(distances))
    p90 = float(np.percentile(distances, 90))
    close_fraction = float(np.mean(distances <= 250.0))
    source_coverage = len(merged) / max(1, len(alternative))
    era_coverage = len(merged) / max(1, len(era_track))
    eligible = (
        median <= MAXIMUM_MEDIAN_DISTANCE_KM
        and p90 <= MAXIMUM_P90_DISTANCE_KM
        and close_fraction >= MINIMUM_CLOSE_FRACTION
    )
    score = (
        median
        + 0.35 * p90
        + 100.0 * (1.0 - close_fraction)
        + 75.0 * (1.0 - min(source_coverage, era_coverage))
    )
    return {
        "era5_track_id": int(era_track_id) if str(era_track_id).isdigit() else str(era_track_id),
        "overlap_hours": int(len(merged)),
        "median_distance_km": round(median, 2),
        "p90_distance_km": round(p90, 2),
        "within_250km_fraction": round(close_fraction, 4),
        "source_coverage_fraction": round(source_coverage, 4),
        "era5_coverage_fraction": round(era_coverage, 4),
        "score": round(score, 3),
        "eligible": bool(eligible),
    }


def candidate_metrics(alternative: pd.DataFrame, era: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for era_track_id, era_track in era.groupby("track_id", sort=False):
        metrics = track_pair_metrics(alternative, era_track, era_track_id)
        if metrics is not None:
            output.append(metrics)
    return sorted(output, key=lambda item: (not item["eligible"], item["score"], -item["overlap_hours"]))


def match_tracks(alternative: pd.DataFrame, era: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    era_groups = {
        track_id: group[["time", "longitude", "latitude"]].copy()
        for track_id, group in era.groupby("track_id", sort=False)
    }
    era_order = {track_id: index for index, track_id in enumerate(era_groups)}
    era_by_time = {
        pd.Timestamp(timestamp): tuple(track_ids)
        for timestamp, track_ids in era.groupby("time", sort=False)["track_id"].unique().items()
    }
    for source_track_id, track in alternative.groupby("track_id", sort=False):
        possible_ids: set[Any] = set()
        for timestamp in pd.DatetimeIndex(track["time"]).unique():
            possible_ids.update(era_by_time.get(pd.Timestamp(timestamp), ()))
        metrics = [
            value
            for era_track_id in sorted(possible_ids, key=era_order.__getitem__)
            if (value := track_pair_metrics(track, era_groups[era_track_id], era_track_id)) is not None
        ]
        metrics.sort(key=lambda item: (not item["eligible"], item["score"], -item["overlap_hours"]))
        eligible = [item for item in metrics if item["eligible"]]
        if not eligible:
            rejected.append({"source_track_id": str(source_track_id), "reason": "no_confident_era5_match"})
            continue
        best = eligible[0]
        ambiguous = len(eligible) > 1 and float(eligible[1]["score"]) - float(best["score"]) < 60.0
        record = {
            **best,
            "source_track_id": str(source_track_id),
            "ambiguous": ambiguous,
            "second_best_score_margin": (
                round(float(eligible[1]["score"]) - float(best["score"]), 3)
                if len(eligible) > 1 else None
            ),
        }
        if ambiguous:
            rejected.append({**record, "reason": "ambiguous_era5_identity"})
        else:
            candidates.append(record)

    # A source-native identity can be selected once, and each ERA5 event gets
    # only its strongest source-native counterpart. This keeps Compare mode
    # honest when either catalogue fragments or duplicates a physical event.
    selected: list[dict[str, Any]] = []
    for era_track_id, group in pd.DataFrame(candidates).groupby("era5_track_id", sort=False) if candidates else []:
        records = group.sort_values(["score", "overlap_hours"], ascending=[True, False]).to_dict("records")
        selected.append(records[0])
        for record in records[1:]:
            rejected.append({**record, "reason": "weaker_source_track_for_same_era5_event"})
    return selected, rejected


def compact_track(track: pd.DataFrame) -> list[list[Any]]:
    output = []
    for row in track.itertuples(index=False):
        timestamp = pd.Timestamp(row.time)
        epoch_hour = int(timestamp.timestamp() // 3600)
        source = str(getattr(row, "position_source", "observed")).lower()
        output.append([
            epoch_hour,
            round(float(row.longitude), 3),
            round(float(row.latitude), 3),
            "o" if source == "observed" else "i",
        ])
    return output


def build_asset(source: str, linked_path: Path, era_path: Path) -> dict[str, Any]:
    alternative = normalise_tracks(pd.read_csv(linked_path), source=source)
    era = normalise_tracks(pd.read_parquet(era_path), source="ERA5")
    era = era.loc[era["time"].between(alternative["time"].min(), alternative["time"].max())]
    selected, rejected = match_tracks(alternative, era)
    selected_ids = {record["source_track_id"] for record in selected}
    tracks = {
        str(track_id): compact_track(track)
        for track_id, track in alternative.groupby("track_id", sort=False)
        if str(track_id) in selected_ids
    }
    return {
        "schema": MATCH_SCHEMA,
        "source": source,
        "generated_utc": utc_now(),
        "coverage_start_utc": alternative["time"].min().isoformat() + "Z",
        "coverage_end_utc": alternative["time"].max().isoformat() + "Z",
        "method": {
            "identity_basis": "ERA5 v5.6 event identity; alternative-reanalysis geometry is source-native",
            "minimum_overlap_hours": MINIMUM_OVERLAP_HOURS,
            "maximum_median_distance_km": MAXIMUM_MEDIAN_DISTANCE_KM,
            "maximum_p90_distance_km": MAXIMUM_P90_DISTANCE_KM,
            "minimum_within_250km_fraction": MINIMUM_CLOSE_FRACTION,
            "ambiguity_margin": 60.0,
        },
        "matches": sorted(selected, key=lambda item: (str(item["era5_track_id"]), item["score"])),
        "tracks": tracks,
        "qa": {
            "source_tracks": int(alternative["track_id"].nunique()),
            "selected_matches": len(selected),
            "rejected_or_secondary_matches": len(rejected),
            "rejections": rejected,
        },
    }


def atomic_gzip_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as stream:
            stream.write(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("merra2", "imdaa", "jra55", "erainterim"), required=True)
    parser.add_argument("--linked", type=Path, required=True)
    parser.add_argument("--era5", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asset = build_asset(args.source, args.linked, args.era5)
    atomic_gzip_json(args.output, asset)
    print(json.dumps({key: asset[key] for key in ("source", "coverage_start_utc", "coverage_end_utc", "qa")}, indent=2))


if __name__ == "__main__":
    main()
