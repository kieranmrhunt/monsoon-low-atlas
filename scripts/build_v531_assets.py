#!/usr/bin/env python3
"""Build compact browser assets from the LPS v5.3.1 fixed-core catalogue."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


TRACK_FIELDS = [
    "id",
    "start_ms",
    "end_ms",
    "start_year",
    "month_mask",
    "n_rows",
    "duration_hours",
    "category",
    "pct_vort",
    "pct_wind",
    "pct_deficit",
    "pct_mslp_depth",
    "pct_precip",
    "peak_vort_x10",
    "peak_precip_x10",
    "peak_wind_x10",
    "peak_deficit_x10",
    "min_mslp_x10",
    "gen_lat_x1000",
    "gen_lon_x1000",
    "end_lat_x1000",
    "end_lon_x1000",
    "distance_km",
    "top_state_idx",
    "top_state_mm_x10",
    "peak_q850_x10",
    "peak_rh850_x10",
    "observed_positions",
    "qualifying_positions",
    "posterior_fraction_x1000",
    "occupancy_fraction_x1000",
    "stitch_count",
    "max_missing_run_hours",
]

SERIES_FIELDS = [
    "hours_since_genesis",
    "precip24_x10",
    "vort_smooth_x10",
    "max_wind_x10",
    "mslp_x10",
    "pressure_deficit_x10",
    "q850_x10",
    "rh850_derived_x10",
    "t850_x10",
    "category",
]

PARQUET_COLUMNS = [
    "track_id",
    "time",
    "lon",
    "lat",
    "position_source",
    "candidate_diagnostics_available",
    "imd_category",
    "max_vort_smoothed",
    "precip_24hr",
    "max_wind",
    "min_mslp",
    "pressure_deficit_hpa",
    "q850_mean_gkg",
    "rh850_mean_pct",
    "t850_mean_k",
    "track_speed_ms",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--track-summary", required=True, type=Path)
    parser.add_argument("--release-summary", required=True, type=Path)
    parser.add_argument("--template-core", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_gzip_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def finite(value, fallback: float = 0.0) -> float:
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else fallback


def scaled(value, scale: float, missing=None):
    if pd.isna(value) or not math.isfinite(float(value)):
        return missing
    return int(round(float(value) * scale))


def scaled_series(values: pd.Series, scale: float) -> list[int | None]:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return [None if not math.isfinite(value) else int(round(value * scale)) for value in numeric]


def rank_percentiles(values: list[float], ascending: bool = True) -> list[int]:
    series = pd.Series(values, dtype=float)
    ranks = series.rank(method="average", pct=True, ascending=ascending) * 100
    return [0 if pd.isna(value) else int(round(value)) for value in ranks]


def encode_polyline(latitudes: np.ndarray, longitudes: np.ndarray) -> str:
    output: list[str] = []
    previous_lat = 0
    previous_lon = 0

    def append_value(delta: int) -> None:
        value = ~(delta << 1) if delta < 0 else delta << 1
        while value >= 0x20:
            output.append(chr((0x20 | (value & 0x1F)) + 63))
            value >>= 5
        output.append(chr(value + 63))

    for latitude, longitude in zip(latitudes, longitudes):
        current_lat = int(round(float(latitude) * 10000))
        current_lon = int(round(float(longitude) * 10000))
        append_value(current_lat - previous_lat)
        append_value(current_lon - previous_lon)
        previous_lat = current_lat
        previous_lon = current_lon
    return "".join(output)


def haversine_steps(latitudes: np.ndarray, longitudes: np.ndarray) -> np.ndarray:
    if len(latitudes) < 2:
        return np.array([], dtype=float)
    lat1 = np.radians(latitudes[:-1])
    lat2 = np.radians(latitudes[1:])
    delta_lat = lat2 - lat1
    delta_lon = np.radians(longitudes[1:] - longitudes[:-1])
    a = np.sin(delta_lat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * np.arctan2(np.sqrt(a), np.sqrt(np.maximum(0, 1 - a)))


def month_of_peak(group: pd.DataFrame, field: str, minimum: bool = False) -> int:
    valid = group.loc[group["candidate_diagnostics_available"] & group[field].notna()]
    if valid.empty:
        return int(group.iloc[0]["month"])
    index = valid[field].idxmin() if minimum else valid[field].idxmax()
    return int(group.loc[index, "month"])


def posterior_runs(observed: np.ndarray) -> list[list[int]]:
    runs: list[list[int]] = []
    start = None
    for index, is_observed in enumerate(observed):
        if not is_observed and start is None:
            start = index
        if is_observed and start is not None:
            runs.append([start, index - 1])
            start = None
    if start is not None:
        runs.append([start, len(observed) - 1])
    return runs


def dump_hashed(payload: dict, stem: str, output_dir: Path) -> tuple[str, int, int]:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()[:12]
    filename = f"{stem}.{digest}.json.gz"
    (output_dir / filename).write_bytes(compressed)
    return filename, len(raw), len(compressed)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    release = json.loads(args.release_summary.read_text(encoding="utf-8"))
    summary = pd.read_csv(args.track_summary)
    template = read_gzip_json(args.template_core)

    table = pq.read_table(args.parquet, columns=PARQUET_COLUMNS)
    data = table.to_pandas()
    data["time"] = pd.to_datetime(data["time"], utc=True)
    data.sort_values(["track_id", "time"], kind="mergesort", inplace=True)
    data["month"] = data["time"].dt.month

    expected = release["qa"]
    if len(data) != int(expected["rows"]):
        raise ValueError(f"Parquet row count {len(data)} != release QA {expected['rows']}")
    if data["track_id"].nunique() != int(expected["tracks"]):
        raise ValueError("Parquet track count does not match the release summary")
    if data.duplicated(["track_id", "time"]).any():
        raise ValueError("Duplicate track/time rows found")
    if set(data["track_id"].unique()) != set(summary["track_id"]):
        raise ValueError("Parquet and track-summary identities differ")

    diagnostics = data["candidate_diagnostics_available"].astype(bool)
    if int(diagnostics.sum()) != int(expected["observed_rows"]):
        raise ValueError("Observed-row count does not match release QA")
    physics = [
        "imd_category",
        "max_vort_smoothed",
        "precip_24hr",
        "max_wind",
        "min_mslp",
        "pressure_deficit_hpa",
        "q850_mean_gkg",
        "t850_mean_k",
    ]
    if data.loc[~diagnostics, physics].notna().any().any():
        raise ValueError("Posterior rows unexpectedly contain detector physics")
    if data.loc[diagnostics, physics].isna().any().any():
        raise ValueError("Observed rows are missing required detector physics")

    groups = {int(track_id): group for track_id, group in data.groupby("track_id", sort=False)}
    summaries = summary.set_index("track_id", drop=False)

    peak_vort: list[float] = []
    peak_wind: list[float] = []
    peak_deficit: list[float] = []
    min_mslp: list[float] = []
    peak_precip: list[float] = []
    group_cache: list[dict] = []

    for source in summary.itertuples(index=False):
        track_id = int(source.track_id)
        group = groups[track_id]
        observed = group.loc[group["candidate_diagnostics_available"]]
        latitudes = group["lat"].to_numpy(dtype=float)
        longitudes = group["lon"].to_numpy(dtype=float)
        times = group["time"]
        deltas = times.diff().dt.total_seconds().div(3600).to_numpy(dtype=float)
        step_km = haversine_steps(latitudes, longitudes)
        step_hours = deltas[1:]
        step_speed = np.divide(
            step_km * 1000,
            step_hours * 3600,
            out=np.full_like(step_km, np.nan),
            where=step_hours > 0,
        )
        breaks = []
        for point_index, (gap, speed) in enumerate(zip(step_hours, step_speed), start=1):
            if gap > 6 or speed > 35:
                breaks.append([point_index, round(float(gap), 1), round(float(speed), 1)])

        metrics = {
            "vort": float(observed["max_vort_smoothed"].max()),
            "wind": float(observed["max_wind"].max()),
            "deficit": float(observed["pressure_deficit_hpa"].max()),
            "mslp": float(observed["min_mslp"].min()),
            "precip": float(observed["precip_24hr"].max()),
            "q850": float(observed["q850_mean_gkg"].max()),
        }
        peak_vort.append(metrics["vort"])
        peak_wind.append(metrics["wind"])
        peak_deficit.append(metrics["deficit"])
        min_mslp.append(metrics["mslp"])
        peak_precip.append(metrics["precip"])
        group_cache.append(
            {
                "source": source,
                "group": group,
                "observed": observed,
                "latitudes": latitudes,
                "longitudes": longitudes,
                "times": times,
                "metrics": metrics,
                "distance_km": int(round(float(np.nansum(step_km)))),
                "max_speed_ms": float(np.nanmax(group["track_speed_ms"].to_numpy(dtype=float))),
                "breaks": breaks,
                "posterior_runs": posterior_runs(group["candidate_diagnostics_available"].to_numpy(dtype=bool)),
            }
        )

    percentiles = {
        "vort": rank_percentiles(peak_vort),
        "wind": rank_percentiles(peak_wind),
        "deficit": rank_percentiles(peak_deficit),
        "mslp": rank_percentiles(min_mslp, ascending=False),
        "precip": rank_percentiles(peak_precip),
    }

    tracks = []
    paths = []
    qc = []
    breaks = []
    bounds = []
    peak_months = []
    posterior = []
    detail_series = []

    for index, cached in enumerate(group_cache):
        source = cached["source"]
        group = cached["group"]
        metrics = cached["metrics"]
        latitudes = cached["latitudes"]
        longitudes = cached["longitudes"]
        start = group.iloc[0]["time"]
        end = group.iloc[-1]["time"]
        month_mask = 0
        for month in sorted(set(int(value) for value in group["month"])):
            month_mask |= 1 << (month - 1)

        category_raw = int(round(finite(source.max_imd_category, 1)))
        category = min(6, max(1, category_raw))
        occupancy = finite(source.occupancy_fraction)
        posterior_fraction = finite(source.posterior_row_fraction)
        maximum_gap = finite(source.max_gap_hours)
        missing_run = finite(source.max_missing_run_hours)
        maximum_speed = cached["max_speed_ms"]
        distance = cached["distance_km"]

        flags = 0
        if missing_run > 24:
            flags |= 1
        if maximum_speed > 30:
            flags |= 2
        if finite(source.duration_hours_observed) > 30 * 24:
            flags |= 4
        if distance > 8000:
            flags |= 8
        if occupancy < 0.5:
            flags |= 16
        if occupancy >= 0.75 and missing_run <= 12 and maximum_speed <= 30:
            severity = 0
        elif occupancy >= 0.5 and missing_run <= 36 and maximum_speed <= 35:
            severity = 1
        else:
            severity = 2

        tracks.append(
            [
                int(source.track_id),
                int(start.value // 1_000_000),
                int(end.value // 1_000_000),
                int(source.genesis_year),
                month_mask,
                int(source.n_rows),
                int(round(finite(source.duration_hours_observed))),
                category,
                percentiles["vort"][index],
                percentiles["wind"][index],
                percentiles["deficit"][index],
                percentiles["mslp"][index],
                percentiles["precip"][index],
                scaled(metrics["vort"], 10, 0),
                scaled(metrics["precip"], 10, 0),
                scaled(metrics["wind"], 10, 0),
                scaled(metrics["deficit"], 10, 0),
                scaled(metrics["mslp"], 10, 0),
                scaled(source.genesis_lat, 1000, 0),
                scaled(source.genesis_lon, 1000, 0),
                scaled(source.lysis_lat, 1000, 0),
                scaled(source.lysis_lon, 1000, 0),
                distance,
                -1,
                -1,
                scaled(metrics["q850"], 10, 0),
                -1,
                int(source.observed_positions),
                int(source.qualifying_positions),
                scaled(posterior_fraction, 1000, 0),
                scaled(occupancy, 1000, 0),
                int(source.stitch_count),
                int(round(missing_run)),
            ]
        )
        paths.append(encode_polyline(latitudes, longitudes))
        qc.append([round(maximum_gap, 1), round(maximum_speed, 1), int(round(occupancy * 100)), flags, severity, len(cached["breaks"])])
        breaks.append(cached["breaks"])
        bounds.append(
            [
                round(float(np.min(longitudes)), 4),
                round(float(np.min(latitudes)), 4),
                round(float(np.max(longitudes)), 4),
                round(float(np.max(latitudes)), 4),
            ]
        )
        peak_months.append(
            [
                month_of_peak(group, "precip_24hr"),
                month_of_peak(group, "max_vort_smoothed"),
                month_of_peak(group, "max_wind"),
                month_of_peak(group, "min_mslp", minimum=True),
                month_of_peak(group, "pressure_deficit_hpa"),
            ]
        )
        posterior.append(cached["posterior_runs"])

        hours = ((group["time"] - start).dt.total_seconds() / 3600).round().astype(int).tolist()
        detail_series.append(
            [
                hours,
                scaled_series(group["precip_24hr"], 10),
                scaled_series(group["max_vort_smoothed"], 10),
                scaled_series(group["max_wind"], 10),
                scaled_series(group["min_mslp"], 10),
                scaled_series(group["pressure_deficit_hpa"], 10),
                scaled_series(group["q850_mean_gkg"], 10),
                scaled_series(group["rh850_mean_pct"], 10),
                scaled_series(group["t850_mean_k"], 10),
                scaled_series(group["imd_category"].clip(upper=6), 1),
            ]
        )

    coverage_start = data["time"].min().isoformat().replace("+00:00", "Z")
    coverage_end = data["time"].max().isoformat().replace("+00:00", "Z")
    built_utc = datetime.now(timezone.utc).isoformat()
    core = {
        "meta": {
            "title": "LPS v5.3.1 ERA5 fixed-core South Asian low-pressure-system catalogue",
            "rows": int(len(data)),
            "observed_rows": int(diagnostics.sum()),
            "posterior_rows": int((~diagnostics).sum()),
            "tracks": int(len(tracks)),
            "columns": int(expected["columns"]),
            "state_columns": 0,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "lon_min": round(float(data["lon"].min()), 4),
            "lon_max": round(float(data["lon"].max()), 4),
            "lat_min": round(float(data["lat"].min()), 4),
            "lat_max": round(float(data["lat"].max()), 4),
            "row_grain": "One row per hourly linked LPS position/time: observed detector fix or gap posterior.",
            "source_dataset": "ERA5-derived LPS v5.3.1 fixed-core enriched catalogue",
            "catalogue_version": "v5.3.1",
            "schema": release["schema"],
            "atlas_version": "3.0.0",
            "built_utc": built_utc,
            "catalogue_completed_utc": release["completed_at_utc"],
            "default_complete_end_year": 2025,
            "core_catalogue_sha256": sha256(args.parquet),
            "track_summary_sha256": sha256(args.track_summary),
            "release_summary_sha256": sha256(args.release_summary),
            "sources": {
                "live_atlas": "https://kieranmrhunt.github.io/monsoon-low-atlas/",
                "release_summary": "data/lps-v5.3.1-release-summary.json",
            },
        },
        "states": template["states"],
        "state_slugs": template["state_slugs"],
        "state_available": [False] * len(template["states"]),
        "cat_labels": {
            "1": "Low",
            "2": "Depression",
            "3": "Deep Depression",
            "4": "Cyclonic Storm",
            "5": "Severe Cyclonic Storm",
            "6": "Very Severe Cyclonic Storm or stronger",
        },
        "track_fields": TRACK_FIELDS,
        "series_fields": SERIES_FIELDS,
        "tracks": tracks,
        "paths": paths,
        "qc_fields": ["max_gap_h", "max_speed_ms", "coverage_pct", "flags", "severity", "break_count"],
        "qc": qc,
        "breaks": breaks,
        "posterior_runs": posterior,
        "bounds": bounds,
        "peak_month_fields": ["rain", "vort", "wind", "mslp", "deficit"],
        "peak_months": peak_months,
        "crosswalk": [None] * len(tracks),
        "ibtracs_tracks": {},
        "geo": template["geo"],
    }
    detail = {
        "series": detail_series,
        "profiles": [],
        "profile_fields": [],
        "profile_bins": 0,
        "state_max_x10": [[] for _ in tracks],
        "state_pct": [[] for _ in tracks],
    }

    for index, series in enumerate(detail_series):
        if len(paths[index]) == 0 or any(len(values) != len(series[0]) for values in series):
            raise ValueError(f"Series/path alignment failed for track {tracks[index][0]}")

    core_name, core_raw, core_gzip = dump_hashed(core, "atlas-core", args.output_dir)
    detail_name, detail_raw, detail_gzip = dump_hashed(detail, "atlas-detail", args.output_dir)
    manifest = {
        "built_utc": built_utc,
        "catalogue_version": "v5.3.1",
        "core": core_name,
        "detail": detail_name,
        "core_uncompressed_bytes": core_raw,
        "core_compressed_bytes": core_gzip,
        "detail_uncompressed_bytes": detail_raw,
        "detail_compressed_bytes": detail_gzip,
        "qa": {
            "tracks": len(tracks),
            "rows": len(data),
            "observed_rows": int(diagnostics.sum()),
            "posterior_rows": int((~diagnostics).sum()),
            "duplicate_track_times": int(data.duplicated(["track_id", "time"]).sum()),
            "all_observed_rows_have_physics": bool(not data.loc[diagnostics, physics].isna().any().any()),
            "no_posterior_rows_have_physics": bool(not data.loc[~diagnostics, physics].notna().any().any()),
        },
    }
    (args.output_dir / "atlas-build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
