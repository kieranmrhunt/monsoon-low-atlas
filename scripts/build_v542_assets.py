#!/usr/bin/env python3
"""Build compact browser assets from the LPS v5.4.2 recall-first core catalogue."""

from __future__ import annotations

import argparse
import base64
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
    "top_state_mean_x10",
    "peak_q850_x10",
    "peak_rh850_x10",
    "observed_positions",
    "qualifying_positions",
    "posterior_fraction_x1000",
    "occupancy_fraction_x1000",
    "stitch_count",
    "max_missing_run_hours",
    "rain_days",
]

SERIES_FIELDS = [
    "hours_since_genesis",
    "precip24_x10",
    "vort_smooth_x10",
    "max_wind_x10",
    "mslp_x10",
    "pressure_deficit_x10",
    "q850_x10",
    "rh850_x10",
    "t850_x10",
    "category",
]

PARQUET_COLUMNS = [
    "track_id",
    "event_id",
    "continuity_parent_track_id",
    "continuity_segment_number",
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
    "passes_mature_physics",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--qa", required=True, type=Path)
    parser.add_argument("--completion-audit", required=True, type=Path)
    parser.add_argument("--protocol-amendment", required=True, type=Path)
    parser.add_argument("--template-core", required=True, type=Path)
    parser.add_argument("--ibtracs-crosswalk", type=Path)
    parser.add_argument("--rainfall-data", required=True, type=Path)
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


def read_dashboard_data(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    prefix = "window.IMD_RAINFALL_DATA = "
    if not text.startswith(prefix):
        raise ValueError("Unsupported IMD dashboard data wrapper")
    return json.loads(text[len(prefix):].removesuffix(";"))


def state_rainfall_series(
    payload: dict,
    state_slugs: list[str],
) -> tuple[list[dict[int, np.ndarray]], list[bool]]:
    regions = payload["regions"]
    metadata = {item["id"]: item for item in regions["list"]}
    years = [int(value) for value in payload["years"] if 1940 <= int(value) <= 2025]
    if years != list(range(1940, 2026)):
        raise ValueError("IMD daily state rainfall does not cover every LPS catalogue year")

    special = {
        "dadra_and_nagar_haveli": ["dadra-and-nagar-haveli-and-daman-and-diu"],
        "daman_and_diu": ["dadra-and-nagar-haveli-and-daman-and-diu"],
        "nct_of_delhi": ["delhi"],
        "jammu_and_kashmir": ["jammu-and-kashmir", "ladakh"],
    }
    decoded: dict[tuple[str, int], np.ndarray] = {}

    def region_year(region_id: str, year: int) -> np.ndarray:
        key = (region_id, year)
        if key not in decoded:
            raw = base64.b64decode(regions["dailyMeanByYear"][region_id][str(year)])
            values = np.frombuffer(raw, dtype="<u2").astype(float)
            expected = int(regions["dailyLengths"][str(year)])
            if len(values) != expected:
                raise ValueError(f"IMD daily series length mismatch for {region_id} {year}")
            values[values == 65535] = np.nan
            decoded[key] = values
        return decoded[key]

    state_series: list[dict[int, np.ndarray]] = []
    available: list[bool] = []
    for slug in state_slugs:
        region_ids = special.get(slug, [slug.replace("_", "-")])
        region_ids = [region_id for region_id in region_ids if region_id in metadata]
        available.append(bool(region_ids))
        yearly: dict[int, np.ndarray] = {}
        for year in years:
            if not region_ids:
                continue
            arrays = [region_year(region_id, year) for region_id in region_ids]
            if len(arrays) == 1:
                yearly[year] = arrays[0]
                continue
            numerator = np.zeros(len(arrays[0]), dtype=float)
            denominator = np.zeros(len(arrays[0]), dtype=float)
            for region_id, values in zip(region_ids, arrays):
                weight = float(metadata[region_id]["cellCount"])
                valid = np.isfinite(values)
                numerator[valid] += values[valid] * weight
                denominator[valid] += weight
            combined = np.full(len(arrays[0]), np.nan, dtype=float)
            np.divide(numerator, denominator, out=combined, where=denominator > 0)
            yearly[year] = combined
        state_series.append(yearly)
    return state_series, available


def mean_state_rainfall(
    group: pd.DataFrame,
    state_series: list[dict[int, np.ndarray]],
) -> tuple[list[int], int]:
    dates = sorted(set(group["time"].dt.date))
    means: list[int] = []
    for yearly in state_series:
        values = []
        for value in dates:
            series = yearly.get(value.year)
            if series is None:
                continue
            rainfall = series[value.timetuple().tm_yday - 1]
            if math.isfinite(float(rainfall)):
                values.append(float(rainfall))
        means.append(int(round(float(np.mean(values)))) if values else -1)
    return means, len(dates)


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
    valid = group.loc[group[field].notna()]
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


def value_runs(values: np.ndarray) -> list[list[int]]:
    """Run-length encode a small integer value as [start, end, value]."""
    if not len(values):
        return []
    runs: list[list[int]] = []
    start = 0
    current = int(values[0])
    for index in range(1, len(values)):
        value = int(values[index])
        if value == current:
            continue
        runs.append([start, index - 1, current])
        start = index
        current = value
    runs.append([start, len(values) - 1, current])
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

    release = json.loads(args.release_manifest.read_text(encoding="utf-8"))
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    qa_release = json.loads(args.qa.read_text(encoding="utf-8"))
    completion_audit = json.loads(args.completion_audit.read_text(encoding="utf-8"))
    protocol_amendment = json.loads(args.protocol_amendment.read_text(encoding="utf-8"))
    template = read_gzip_json(args.template_core)
    rainfall = read_dashboard_data(args.rainfall_data)
    rainfall_series, state_available = state_rainfall_series(
        rainfall,
        template["state_slugs"],
    )
    ibtracs = (
        json.loads(args.ibtracs_crosswalk.read_text(encoding="utf-8"))
        if args.ibtracs_crosswalk
        else None
    )
    if ibtracs and ibtracs.get("schema") != "lps-ibtracs-v04r01-crosswalk-v1":
        raise ValueError("Unsupported IBTrACS crosswalk schema")
    if ibtracs and ibtracs.get("catalogue_version") != "v5.4.2":
        raise ValueError("IBTrACS crosswalk does not match the v5.4.2 catalogue")
    if release.get("schema") != "lps-v5.4.2-release-manifest-v1":
        raise ValueError("Unsupported release manifest")
    if metadata.get("version") != "5.4.2" or qa_release.get("status") != "pass":
        raise ValueError("v5.4.2 metadata or final QA does not report a passing release")
    if not metadata.get("qa", {}).get("all_release_gates_passed"):
        raise ValueError("v5.4.2 metadata does not report all release gates passing")
    if completion_audit.get("status") != "pass":
        raise ValueError("v5.4.2 completion audit does not report a passing release")
    if (
        protocol_amendment.get("scientific_change") is not False
        or protocol_amendment.get("catalogue_changed") is not False
    ):
        raise ValueError("Unexpected catalogue change in protocol amendment 5")
    source_sha = sha256(args.parquet)
    if source_sha != release["catalogue"]["sha256"] or source_sha != metadata["sha256"]:
        raise ValueError("Parquet SHA-256 does not match the release documents")

    table = pq.read_table(args.parquet, columns=PARQUET_COLUMNS)
    data = table.to_pandas()
    data["time"] = pd.to_datetime(data["time"], utc=True)
    data.sort_values(["continuity_parent_track_id", "time", "track_id"], kind="mergesort", inplace=True)
    data["month"] = data["time"].dt.month

    expected = release["catalogue"]
    if len(data) != int(expected["rows"]):
        raise ValueError(f"Parquet row count {len(data)} != release manifest {expected['rows']}")
    if data["track_id"].nunique() != int(expected["physical_events"]):
        raise ValueError("Physical-event count does not match the release manifest")
    if data["continuity_parent_track_id"].nunique() != int(expected["physical_events"]):
        raise ValueError("Continuity identity count does not match the release manifest")
    if not (
        data["track_id"].eq(data["continuity_parent_track_id"])
        & data["track_id"].eq(data["event_id"])
    ).all():
        raise ValueError("v5.4.2 physical-event identities are not aligned")
    if data.duplicated(["track_id", "time"]).any():
        raise ValueError("Duplicate publication-segment/time rows found")
    if data.duplicated(["continuity_parent_track_id", "time"]).any():
        raise ValueError("Duplicate parent-event/time rows found")

    diagnostics = data["candidate_diagnostics_available"].astype(bool)
    if int(diagnostics.sum()) != int(expected["observed_rows"]):
        raise ValueError("Observed-row count does not match release QA")
    if not (
        (data["position_source"].eq("observed") == diagnostics)
        & (data["position_source"].eq("interpolated") == ~diagnostics)
    ).all():
        raise ValueError("Position source and observed-diagnostics flag disagree")
    physics = [
        "max_vort_smoothed",
        "precip_24hr",
        "max_wind",
        "min_mslp",
        "pressure_deficit_hpa",
        "q850_mean_gkg",
        "rh850_mean_pct",
        "t850_mean_k",
    ]
    if data[physics].isna().any().any():
        raise ValueError("Published rows are missing required final-centre physics")
    if data.loc[diagnostics, "imd_category"].isna().any():
        raise ValueError("Observed rows are missing the persistent IMD-equivalent class")
    if data.loc[~diagnostics, "imd_category"].notna().any():
        raise ValueError("Interpolated rows unexpectedly carry an observed-support class")
    groups = {
        int(parent_id): group.copy()
        for parent_id, group in data.groupby("continuity_parent_track_id", sort=False)
    }

    peak_vort: list[float] = []
    peak_wind: list[float] = []
    peak_deficit: list[float] = []
    min_mslp: list[float] = []
    peak_precip: list[float] = []
    group_cache: list[dict] = []

    for parent_id, group in groups.items():
        observed_mask = group["candidate_diagnostics_available"].to_numpy(dtype=bool)
        observed = group.loc[observed_mask]
        latitudes = group["lat"].to_numpy(dtype=float)
        longitudes = group["lon"].to_numpy(dtype=float)
        times = group["time"]
        deltas = times.diff().dt.total_seconds().div(3600).to_numpy(dtype=float)
        step_km = haversine_steps(latitudes, longitudes)
        step_hours = deltas[1:]
        segment_ids = group["track_id"].to_numpy(dtype=np.int64)
        segment_starts = np.flatnonzero(segment_ids[1:] != segment_ids[:-1]) + 1
        if len(segment_starts) + 1 != group["track_id"].nunique():
            raise ValueError(f"Publication segments interleave for parent event {parent_id}")
        within_segment = segment_ids[1:] == segment_ids[:-1]
        step_speed = np.divide(
            step_km * 1000,
            step_hours * 3600,
            out=np.full_like(step_km, np.nan),
            where=(step_hours > 0) & within_segment,
        )
        breaks = [
            [int(point_index), round(float(deltas[point_index]), 1), 999]
            for point_index in segment_starts
        ]
        valid_speeds = step_speed[within_segment]
        posterior = posterior_runs(observed_mask)
        maximum_missing_run = max(
            (end - start + 1 for start, end in posterior),
            default=0,
        )

        metrics = {
            "vort": float(group["max_vort_smoothed"].max()),
            "wind": float(group["max_wind"].max()),
            "deficit": float(group["pressure_deficit_hpa"].max()),
            "mslp": float(group["min_mslp"].min()),
            "precip": float(group["precip_24hr"].max()),
            "q850": float(group["q850_mean_gkg"].max()),
            "rh850": float(group["rh850_mean_pct"].max()),
        }
        state_means, rain_days = mean_state_rainfall(group, rainfall_series)
        peak_vort.append(metrics["vort"])
        peak_wind.append(metrics["wind"])
        peak_deficit.append(metrics["deficit"])
        min_mslp.append(metrics["mslp"])
        peak_precip.append(metrics["precip"])
        group_cache.append(
            {
                "parent_id": parent_id,
                "group": group,
                "observed": observed,
                "latitudes": latitudes,
                "longitudes": longitudes,
                "times": times,
                "metrics": metrics,
                "state_means": state_means,
                "rain_days": rain_days,
                "distance_km": int(round(float(np.nansum(step_km[within_segment])))),
                "max_speed_ms": float(np.nanmax(valid_speeds)) if len(valid_speeds) else 0,
                "max_gap_hours": float(np.nanmax(step_hours)) if len(step_hours) else 0,
                "segment_count": int(group["track_id"].nunique()),
                "observed_positions": int(observed_mask.sum()),
                "qualifying_positions": int(
                    group.loc[observed_mask, "passes_mature_physics"].fillna(0).gt(0).sum()
                ),
                "posterior_fraction": float((~observed_mask).mean()),
                "occupancy_fraction": float(observed_mask.mean()),
                "max_missing_run_hours": int(maximum_missing_run),
                "breaks": breaks,
                "posterior_runs": posterior,
                "month_runs": value_runs(group["month"].to_numpy(dtype=np.int8)),
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
    point_months = []
    detail_series = []
    state_mean_rows = []

    for index, cached in enumerate(group_cache):
        parent_id = cached["parent_id"]
        group = cached["group"]
        metrics = cached["metrics"]
        latitudes = cached["latitudes"]
        longitudes = cached["longitudes"]
        start = group.iloc[0]["time"]
        end = group.iloc[-1]["time"]
        month_mask = 0
        for month in sorted(set(int(value) for value in group["month"])):
            month_mask |= 1 << (month - 1)

        category_raw = int(round(finite(cached["observed"]["imd_category"].max(), 1)))
        category = min(6, max(1, category_raw))
        occupancy = cached["occupancy_fraction"]
        posterior_fraction = cached["posterior_fraction"]
        maximum_gap = cached["max_gap_hours"]
        missing_run = cached["max_missing_run_hours"]
        maximum_speed = cached["max_speed_ms"]
        distance = cached["distance_km"]
        segment_count = cached["segment_count"]
        duration_hours = float((end - start).total_seconds() / 3600)
        state_means = cached["state_means"]
        available_means = [
            (state_index, value)
            for state_index, value in enumerate(state_means)
            if value >= 0
        ]
        top_state_idx, top_state_mean = (
            max(available_means, key=lambda item: item[1])
            if available_means
            else (-1, -1)
        )

        flags = 0
        if missing_run > 24:
            flags |= 1
        if maximum_speed > 30:
            flags |= 2
        if duration_hours > 30 * 24:
            flags |= 4
        if distance > 8000:
            flags |= 8
        if occupancy < 0.5:
            flags |= 16
        if segment_count > 1:
            flags |= 32
        if segment_count == 1 and occupancy >= 0.75 and missing_run <= 12 and maximum_speed <= 29.5:
            severity = 0
        elif segment_count <= 3 and occupancy >= 0.5 and missing_run <= 36 and maximum_speed <= 29.5:
            severity = 1
        else:
            severity = 2

        tracks.append(
            [
                int(parent_id),
                int(start.value // 1_000_000),
                int(end.value // 1_000_000),
                int(start.year),
                month_mask,
                int(len(group)),
                int(round(duration_hours)),
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
                scaled(group.iloc[0]["lat"], 1000, 0),
                scaled(group.iloc[0]["lon"], 1000, 0),
                scaled(group.iloc[-1]["lat"], 1000, 0),
                scaled(group.iloc[-1]["lon"], 1000, 0),
                distance,
                top_state_idx,
                top_state_mean,
                scaled(metrics["q850"], 10, 0),
                scaled(metrics["rh850"], 10, 0),
                cached["observed_positions"],
                cached["qualifying_positions"],
                scaled(posterior_fraction, 1000, 0),
                scaled(occupancy, 1000, 0),
                segment_count,
                int(round(missing_run)),
                int(cached["rain_days"]),
            ]
        )
        paths.append(encode_polyline(latitudes, longitudes))
        qc.append(
            [
                round(maximum_gap, 1),
                round(maximum_speed, 1),
                int(round(occupancy * 100)),
                flags,
                severity,
                len(cached["breaks"]),
            ]
        )
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
        point_months.append(cached["month_runs"])

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
        state_mean_rows.append(state_means)

    state_matrix = np.asarray(state_mean_rows, dtype=float)
    state_percentiles = np.full(state_matrix.shape, -1, dtype=np.int16)
    for state_index in range(state_matrix.shape[1]):
        valid = state_matrix[:, state_index] >= 0
        if not valid.any():
            continue
        ranks = pd.Series(state_matrix[valid, state_index]).rank(
            method="average",
            pct=True,
        ) * 100
        state_percentiles[valid, state_index] = np.rint(
            ranks.to_numpy()
        ).astype(np.int16)

    coverage_start = data["time"].min().isoformat().replace("+00:00", "Z")
    coverage_end = data["time"].max().isoformat().replace("+00:00", "Z")
    built_utc = datetime.now(timezone.utc).isoformat()
    crosswalk: list[dict | None] = []
    matched_sids: set[str] = set()
    for row in tracks:
        match = ibtracs["matches"].get(str(row[0])) if ibtracs else None
        if match:
            matched_sids.add(match["sid"])
            crosswalk.append({"ib": match})
        else:
            crosswalk.append(None)
    ibtracs_tracks = {
        sid: value for sid, value in (ibtracs["storms"].items() if ibtracs else [])
        if sid in matched_sids
    }
    core = {
        "meta": {
            "title": "LPS v5.4.2 ERA5 recall-first core South Asian low-pressure-system catalogue",
            "rows": int(len(data)),
            "observed_rows": int(diagnostics.sum()),
            "posterior_rows": int((~diagnostics).sum()),
            "tracks": int(len(tracks)),
            "publication_segments": int(data["track_id"].nunique()),
            "publication_splits": 0,
            "columns": int(metadata["schema_columns"]),
            "state_columns": int(sum(state_available)),
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "lon_min": round(float(data["lon"].min()), 4),
            "lon_max": round(float(data["lon"].max()), 4),
            "lat_min": round(float(data["lat"].min()), 4),
            "lat_max": round(float(data["lat"].max()), 4),
            "row_grain": "One row per hourly physical-event position/time: observed detector fix or supported interpolated centre.",
            "identity_grain": "track_id is one hourly-complete physical event; event_id and continuity_parent_track_id are identical aliases.",
            "source_dataset": "ERA5-derived LPS v5.4.2 recall-first core catalogue",
            "catalogue_version": "v5.4.2",
            "schema": release["schema"],
            "atlas_version": "3.5.0",
            "built_utc": built_utc,
            "catalogue_completed_utc": release["publication"]["published_on"],
            "default_complete_end_year": 2025,
            "core_catalogue_sha256": source_sha,
            "release_manifest_sha256": sha256(args.release_manifest),
            "metadata_sha256": sha256(args.metadata),
            "qa_sha256": sha256(args.qa),
            "completion_audit_sha256": sha256(args.completion_audit),
            "protocol_amendment_sha256": sha256(args.protocol_amendment),
            "sources": {
                "live_atlas": "https://kieranmrhunt.github.io/monsoon-low-atlas/",
                "release_summary": "data/lps-v5.4.2-era5-1940-2025-core.release-manifest.json",
                "metadata": "data/lps-v5.4.2-era5-1940-2025-core.metadata.json",
                "qa": "data/lps-v5.4.2-era5-1940-2025-core.qa.json",
                "completion_audit": "data/lps-v5.4.2-era5-1940-2025-core.completion-audit.json",
                "protocol_amendment": "data/lps-v5.4.2-era5-1940-2025-core.protocol-amendment-5.json",
                "quality_report": "data/lps-v5.4.2-era5-1940-2025-core.quality-report.md",
                "ibtracs": "https://www.ncei.noaa.gov/products/international-best-track-archive",
                "state_rainfall": "https://kieranmrhunt.github.io/imd-rainfall-dashboard/",
            },
            "ibtracs_crosswalk": ibtracs["method"] if ibtracs else None,
        },
        "states": template["states"],
        "state_slugs": template["state_slugs"],
        "state_available": state_available,
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
        "point_month_runs": point_months,
        "bounds": bounds,
        "peak_month_fields": ["rain", "vort", "wind", "mslp", "deficit"],
        "peak_months": peak_months,
        "crosswalk": crosswalk,
        "ibtracs_tracks": ibtracs_tracks,
        "geo": template["geo"],
    }
    detail = {
        "series": detail_series,
        "profiles": [],
        "profile_fields": [],
        "profile_bins": 0,
        "state_mean_x10": state_mean_rows,
        "state_pct": state_percentiles.tolist(),
        "state_rainfall": {
            "source": rainfall["meta"]["source"],
            "units": "mm day-1",
            "statistic": "Area-mean daily rainfall averaged over UTC calendar days touched by each LPS physical event.",
            "coverage": [1940, 2025],
            "baseline": rainfall["meta"]["baseline"],
        },
    }

    for index, series in enumerate(detail_series):
        if len(paths[index]) == 0 or any(len(values) != len(series[0]) for values in series):
            raise ValueError(f"Series/path alignment failed for track {tracks[index][0]}")

    core_name, core_raw, core_gzip = dump_hashed(core, "atlas-core", args.output_dir)
    detail_name, detail_raw, detail_gzip = dump_hashed(detail, "atlas-detail", args.output_dir)
    manifest = {
        "built_utc": built_utc,
        "catalogue_version": "v5.4.2",
        "state_rainfall_sha256": sha256(args.rainfall_data),
        "core": core_name,
        "detail": detail_name,
        "core_uncompressed_bytes": core_raw,
        "core_compressed_bytes": core_gzip,
        "detail_uncompressed_bytes": detail_raw,
        "detail_compressed_bytes": detail_gzip,
        "qa": {
            "tracks": len(tracks),
            "publication_segments": int(data["track_id"].nunique()),
            "publication_splits": sum(len(value) for value in breaks),
            "rows": len(data),
            "observed_rows": int(diagnostics.sum()),
            "posterior_rows": int((~diagnostics).sum()),
            "duplicate_segment_times": int(data.duplicated(["track_id", "time"]).sum()),
            "duplicate_parent_times": int(
                data.duplicated(["continuity_parent_track_id", "time"]).sum()
            ),
            "all_rows_have_final_centre_physics": bool(not data[physics].isna().any().any()),
            "state_rainfall_columns": int(sum(state_available)),
            "tracks_with_state_rainfall": int(
                np.any(state_matrix >= 0, axis=1).sum()
            ),
            "ibtracs_matched_tracks": sum(value is not None for value in crosswalk),
            "ibtracs_named_tracks": sum(
                value is not None
                and value["ib"]["confidence"] in {"high", "medium"}
                and bool(ibtracs_tracks.get(value["ib"]["sid"], {}).get("name"))
                for value in crosswalk
            ),
        },
    }
    (args.output_dir / "atlas-build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if manifest["qa"]["publication_splits"] != 0:
        raise ValueError("v5.4.2 physical events should not contain publication splits")
    if not release["continuity"]["complete_hourly_spans"]:
        raise ValueError("Release manifest does not report complete hourly spans")
    if int(release["continuity"]["non_hourly_steps"]) != 0:
        raise ValueError("Release manifest reports non-hourly steps")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
