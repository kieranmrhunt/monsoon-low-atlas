#!/usr/bin/env python3
"""Match LPS v5.4 identities to IBTrACS tracks using observed support only."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


IBTRACS_COLUMNS = ["SID", "SEASON", "BASIN", "NAME", "ISO_TIME", "LAT", "LON"]
PLACEHOLDER_NAMES = {"", "UNNAMED", "NOT_NAMED", "UNKNOWN", "NAN", "INVEST"}
MAX_TIME_DELTA_HOURS = 3
MAX_MEDIAN_KM = 225
MAX_P90_KM = 350


@dataclass
class Storm:
    sid: str
    basin: str
    name: str
    times_ns: np.ndarray
    latitudes: np.ndarray
    longitudes: np.ndarray

    @property
    def start_ns(self) -> int:
        return int(self.times_ns[0])

    @property
    def end_ns(self) -> int:
        return int(self.times_ns[-1])

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            float(np.min(self.longitudes)),
            float(np.min(self.latitudes)),
            float(np.max(self.longitudes)),
            float(np.max(self.latitudes)),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--ibtracs", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def encode_polyline(latitudes: np.ndarray, longitudes: np.ndarray) -> str:
    output: list[str] = []
    previous_latitude = 0
    previous_longitude = 0

    def append_value(delta: int) -> None:
        value = ~(delta << 1) if delta < 0 else delta << 1
        while value >= 0x20:
            output.append(chr((0x20 | (value & 0x1F)) + 63))
            value >>= 5
        output.append(chr(value + 63))

    for latitude, longitude in zip(latitudes, longitudes):
        encoded_latitude = int(round(float(latitude) * 10000))
        encoded_longitude = int(round(float(longitude) * 10000))
        append_value(encoded_latitude - previous_latitude)
        append_value(encoded_longitude - previous_longitude)
        previous_latitude = encoded_latitude
        previous_longitude = encoded_longitude
    return "".join(output)


def display_name(value: object) -> str:
    raw = str(value or "").strip().upper()
    if raw in PLACEHOLDER_NAMES:
        return ""
    aliases = {"KYAAR": "KYARR"}
    parts: list[str] = []
    for item in raw.split(":"):
        cleaned = item.removesuffix("-GU").strip()
        cleaned = aliases.get(cleaned, cleaned)
        if cleaned and cleaned not in PLACEHOLDER_NAMES and cleaned not in parts:
            parts.append(cleaned)
    return " / ".join(item.title() for item in parts)


def read_ibtracs(paths: list[Path]) -> list[Storm]:
    frames = []
    for path in paths:
        frame = pd.read_csv(
            path,
            skiprows=[1],
            usecols=IBTRACS_COLUMNS,
            low_memory=False,
        )
        frame["ISO_TIME"] = pd.to_datetime(frame["ISO_TIME"], utc=True, errors="coerce")
        frame["LAT"] = pd.to_numeric(frame["LAT"], errors="coerce")
        frame["LON"] = pd.to_numeric(frame["LON"], errors="coerce")
        frame = frame.loc[
            frame["ISO_TIME"].notna()
            & frame["LAT"].notna()
            & frame["LON"].notna()
            & frame["ISO_TIME"].dt.year.between(1940, 2025)
            & frame["LAT"].between(-15, 55)
            & frame["LON"].between(35, 135)
        ]
        frames.append(frame)

    data = pd.concat(frames, ignore_index=True)
    data.sort_values(["SID", "ISO_TIME"], kind="mergesort", inplace=True)
    data.drop_duplicates(["SID", "ISO_TIME"], keep="first", inplace=True)
    storms: list[Storm] = []
    for sid, group in data.groupby("SID", sort=False):
        if len(group) < 2:
            continue
        names = [display_name(value) for value in group["NAME"]]
        name = next((value for value in names if value), "")
        basins = [str(value).strip() for value in group["BASIN"] if str(value).strip()]
        storms.append(
            Storm(
                sid=str(sid),
                basin=basins[0] if basins else "",
                name=name,
                times_ns=group["ISO_TIME"].to_numpy(dtype="datetime64[ns]").astype("int64"),
                latitudes=group["LAT"].to_numpy(dtype=float),
                longitudes=group["LON"].to_numpy(dtype=float),
            )
        )
    return storms


def haversine_km(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    radius = 6371.0088
    first_latitude = np.radians(lat1)
    second_latitude = np.radians(lat2)
    delta_latitude = second_latitude - first_latitude
    delta_longitude = np.radians(lon2 - lon1)
    value = (
        np.sin(delta_latitude / 2) ** 2
        + np.cos(first_latitude) * np.cos(second_latitude) * np.sin(delta_longitude / 2) ** 2
    )
    return radius * 2 * np.arctan2(np.sqrt(value), np.sqrt(np.maximum(0, 1 - value)))


def separated_bounds(first: tuple[float, float, float, float], second: tuple[float, float, float, float], buffer_degrees: float = 4) -> bool:
    return (
        first[2] + buffer_degrees < second[0]
        or second[2] + buffer_degrees < first[0]
        or first[3] + buffer_degrees < second[1]
        or second[3] + buffer_degrees < first[1]
    )


def compare_track(
    track_times_ns: np.ndarray,
    track_latitudes: np.ndarray,
    track_longitudes: np.ndarray,
    storm: Storm,
) -> dict | None:
    inside = (storm.times_ns >= track_times_ns[0]) & (storm.times_ns <= track_times_ns[-1])
    storm_times = storm.times_ns[inside]
    if len(storm_times) < 3:
        return None
    storm_latitudes = storm.latitudes[inside]
    storm_longitudes = storm.longitudes[inside]
    right = np.searchsorted(track_times_ns, storm_times, side="left")
    right = np.clip(right, 0, len(track_times_ns) - 1)
    left = np.clip(right - 1, 0, len(track_times_ns) - 1)
    choose_left = np.abs(track_times_ns[left] - storm_times) <= np.abs(track_times_ns[right] - storm_times)
    nearest = np.where(choose_left, left, right)
    delta_hours = np.abs(track_times_ns[nearest] - storm_times) / 3_600_000_000_000
    valid = delta_hours <= MAX_TIME_DELTA_HOURS
    if int(valid.sum()) < 3:
        return None
    distances = haversine_km(
        track_latitudes[nearest[valid]],
        track_longitudes[nearest[valid]],
        storm_latitudes[valid],
        storm_longitudes[valid],
    )
    overlap_hours = float((storm_times[valid][-1] - storm_times[valid][0]) / 3_600_000_000_000)
    if overlap_hours < 6:
        return None
    median_km = float(np.median(distances))
    p90_km = float(np.quantile(distances, 0.9))
    if median_km > MAX_MEDIAN_KM or p90_km > MAX_P90_KM:
        return None
    score = median_km + 0.35 * p90_km - min(60, overlap_hours * 0.55)
    return {
        "sid": storm.sid,
        "median_km": round(median_km, 1),
        "p90_km": round(p90_km, 1),
        "overlap": int(valid.sum()),
        "overlap_hours": round(overlap_hours, 1),
        "score": round(score, 1),
    }


def confidence(best: dict, margin: float) -> str:
    if (
        best["median_km"] <= 120
        and best["p90_km"] <= 220
        and best["overlap"] >= 4
        and best["overlap_hours"] >= 12
        and margin >= 25
    ):
        return "high"
    if (
        best["median_km"] <= 200
        and best["p90_km"] <= 320
        and best["overlap"] >= 3
        and best["overlap_hours"] >= 6
        and margin >= 15
    ):
        return "medium"
    return "low"


def main() -> None:
    args = parse_args()
    table = pq.read_table(
        args.parquet,
        columns=[
            "track_id",
            "time",
            "lat_smooth_global_v54",
            "lon_smooth_global_v54",
            "candidate_diagnostics_available",
        ],
    )
    catalogue = table.to_pandas()
    catalogue.rename(
        columns={"lat_smooth_global_v54": "lat", "lon_smooth_global_v54": "lon"},
        inplace=True,
    )
    catalogue["time"] = pd.to_datetime(catalogue["time"], utc=True)
    catalogue = catalogue.loc[catalogue["candidate_diagnostics_available"].astype(bool)].copy()
    catalogue.sort_values(["track_id", "time"], kind="mergesort", inplace=True)

    storms = read_ibtracs(args.ibtracs)
    storms_by_year: dict[int, set[int]] = defaultdict(set)
    for index, storm in enumerate(storms):
        start_year = pd.Timestamp(storm.start_ns, tz="UTC").year
        end_year = pd.Timestamp(storm.end_ns, tz="UTC").year
        for year in range(start_year, end_year + 1):
            storms_by_year[year].add(index)

    matches: dict[str, dict] = {}
    track_start: dict[str, int] = {}
    for track_id, group in catalogue.groupby("track_id", sort=False):
        times_ns = group["time"].to_numpy(dtype="datetime64[ns]").astype("int64")
        latitudes = group["lat"].to_numpy(dtype=float)
        longitudes = group["lon"].to_numpy(dtype=float)
        if len(times_ns) < 3:
            continue
        track_key = str(int(track_id))
        track_start[track_key] = int(times_ns[0])
        track_bounds = (
            float(np.min(longitudes)),
            float(np.min(latitudes)),
            float(np.max(longitudes)),
            float(np.max(latitudes)),
        )
        first_year = group.iloc[0]["time"].year
        last_year = group.iloc[-1]["time"].year
        candidates: list[dict] = []
        candidate_indexes: set[int] = set()
        for year in range(first_year, last_year + 1):
            candidate_indexes.update(storms_by_year.get(year, set()))
        for storm_index in candidate_indexes:
            storm = storms[storm_index]
            if storm.end_ns < times_ns[0] or storm.start_ns > times_ns[-1]:
                continue
            if separated_bounds(track_bounds, storm.bounds):
                continue
            result = compare_track(times_ns, latitudes, longitudes, storm)
            if result:
                candidates.append(result)
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item["score"], item["median_km"], item["sid"]))
        best = candidates[0]
        margin = math.inf if len(candidates) == 1 else candidates[1]["score"] - best["score"]
        best["margin"] = None if not math.isfinite(margin) else round(float(margin), 1)
        best["confidence"] = confidence(best, margin)
        matches[track_key] = best

    by_sid: dict[str, list[str]] = defaultdict(list)
    for track_id, match in matches.items():
        by_sid[match["sid"]].append(track_id)
    for sid, track_ids in by_sid.items():
        track_ids.sort(key=lambda value: (track_start.get(value, 0), int(value)))
        for index, track_id in enumerate(track_ids, start=1):
            matches[track_id]["segment_index"] = index
            matches[track_id]["segment_count"] = len(track_ids)

    matched_sids = {match["sid"] for match in matches.values()}
    storm_payload = {}
    for storm in storms:
        if storm.sid not in matched_sids:
            continue
        storm_payload[storm.sid] = {
            "name": storm.name,
            "basin": storm.basin,
            "start": pd.Timestamp(storm.start_ns, tz="UTC").isoformat(),
            "end": pd.Timestamp(storm.end_ns, tz="UTC").isoformat(),
            "path": encode_polyline(storm.latitudes, storm.longitudes),
        }

    qa = {
        "catalogue_tracks": int(catalogue["track_id"].nunique()),
        "ibtracs_storms_considered": len(storms),
        "matched_tracks": len(matches),
        "high_confidence": sum(match["confidence"] == "high" for match in matches.values()),
        "medium_confidence": sum(match["confidence"] == "medium" for match in matches.values()),
        "low_confidence": sum(match["confidence"] == "low" for match in matches.values()),
        "named_high_or_medium": sum(
            match["confidence"] in {"high", "medium"} and bool(storm_payload[match["sid"]]["name"])
            for match in matches.values()
        ),
    }
    payload = {
        "schema": "lps-v5.4-ibtracs-v04r01-crosswalk-v1",
        "source": "NOAA NCEI IBTrACS v04r01 NI and WP basin CSVs",
        "method": {
            "positions": "Published v5.4 centres at observed-support times only; interpolated positions are excluded.",
            "maximum_time_delta_hours": MAX_TIME_DELTA_HOURS,
            "maximum_median_separation_km": MAX_MEDIAN_KM,
            "maximum_p90_separation_km": MAX_P90_KM,
            "naming_confidence": "Names are displayed for high- and medium-confidence matches only.",
        },
        "qa": qa,
        "matches": matches,
        "storms": storm_payload,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(qa, indent=2))


if __name__ == "__main__":
    main()
