#!/usr/bin/env python3
"""Storm-centred and India-wide CMIP6 LPS precipitation diagnostics."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import xarray as xr
from shapely import Polygon, intersects_xy, make_valid, union_all
from sklearn.neighbors import BallTree

from reanalysis_pipeline.common import TARGET_LATS, TARGET_LONS, sha256

from .model_calendar import TimeAxis
from .summarise import atomic_gzip_json, bootstrap_change


RUN_SCHEMA = "lps-atlas-cmip6-precipitation-impact-v1"
PAIR_SCHEMA = "lps-atlas-cmip6-precipitation-impact-pair-v1"
ENSEMBLE_SCHEMA = "lps-atlas-cmip6-precipitation-impact-ensemble-v1"
EARTH_RADIUS_KM = 6371.0088
EXPOSURE_RADIUS_KM = 800.0
FOOTPRINT_OFFSETS = np.arange(-10.0, 10.01, 1.0, dtype=np.float32)
SEASONS = {
    "all": tuple(range(1, 13)),
    "jjas": (6, 7, 8, 9),
    "mam": (3, 4, 5),
    "ond": (10, 11, 12),
    "djf": (12, 1, 2),
}
INDIA_METRICS = (
    "rainfall_share",
    "climatological_excess_share",
    "month_control_excess_share",
    "exposed_area_day_fraction",
    "exposed_to_all_rain_ratio",
    "all_india_mean_mm_day",
    "exposed_mean_mm_day",
    "heavy_20mm_exposed_cell_day_share",
    "heavy_50mm_exposed_cell_day_share",
    "active_lps",
    "genesis_lps",
)
REGIONAL_RAIN_METRICS = (
    "rainfall_share",
    "climatological_excess_share",
    "month_control_excess_share",
    "exposed_area_day_fraction",
    "exposed_to_all_rain_ratio",
    "regional_mean_mm_day",
    "exposed_mean_mm_day",
    "heavy_20mm_exposed_cell_day_share",
    "heavy_50mm_exposed_cell_day_share",
)
INDIA_REGIONS = {
    "northwest": {
        "label": "Northwest",
        "state_ids": (
            "jammu_and_kashmir", "himachal_pradesh", "punjab", "chandigarh",
            "haryana", "nct_of_delhi", "rajasthan", "gujarat",
        ),
    },
    "north_central": {
        "label": "North-central",
        "state_ids": ("uttar_pradesh", "uttarakhand", "madhya_pradesh", "chhattisgarh"),
    },
    "east": {
        "label": "East",
        "state_ids": ("bihar", "jharkhand", "odisha", "west_bengal"),
    },
    "northeast": {
        "label": "Northeast",
        "state_ids": (
            "arunachal_pradesh", "assam", "meghalaya", "nagaland", "manipur",
            "mizoram", "tripura", "sikkim",
        ),
    },
    "west_coast": {
        "label": "West coast",
        "state_ids": (
            "maharashtra", "goa", "karnataka", "kerala",
            "dadra_and_nagar_haveli", "daman_and_diu",
        ),
    },
    "south_peninsula": {
        "label": "South peninsula",
        "state_ids": ("telangana", "andhra_pradesh", "tamil_nadu", "puducherry"),
    },
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_tsv(path: Path, rows: list[list[object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to create empty task file {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _json_array(values: np.ndarray, digits: int) -> list[Any]:
    rounded = np.round(np.asarray(values, dtype=float), digits)
    return np.where(np.isfinite(rounded), rounded, None).tolist()


def _axis(plan: dict[str, Any]) -> TimeAxis | None:
    record = plan.get("time_axis")
    return TimeAxis.from_record(record) if record else None


def native_components(times: Iterable[Any], axis: TimeAxis | None) -> pd.DataFrame:
    analysis = pd.DatetimeIndex(pd.to_datetime(list(times), errors="raise"))
    if axis is None:
        return pd.DataFrame(
            {
                "year": analysis.year,
                "month": analysis.month,
                "day": analysis.day,
                "hour": analysis.hour,
            }
        )
    native = axis.analysis_to_native(analysis)
    return pd.DataFrame(
        {
            "year": [value.year for value in native],
            "month": [value.month for value in native],
            "day": [value.day for value in native],
            "hour": [value.hour for value in native],
        }
    )


def track_components(frame: pd.DataFrame, axis: TimeAxis | None) -> pd.DataFrame:
    required = {"model_year", "model_month", "model_day", "model_hour"}
    if required.issubset(frame.columns):
        return frame[["model_year", "model_month", "model_day", "model_hour"]].rename(
            columns={name: name.removeprefix("model_") for name in required}
        )[["year", "month", "day", "hour"]].reset_index(drop=True)
    return native_components(frame.time, axis)


def _event_peaks(frame: pd.DataFrame, axis: TimeAxis | None) -> pd.DataFrame:
    work = frame.copy()
    work["time"] = pd.to_datetime(work.time, errors="raise")
    components = track_components(work, axis)
    for column in components:
        work[f"_native_{column}"] = components[column].to_numpy()
    metric = "p95_anomaly_wind_125km_ms" if "p95_anomaly_wind_125km_ms" in work else "max_vort_smoothed"
    work["_peak_metric"] = pd.to_numeric(work[metric], errors="coerce")
    rows = []
    for track_id, group in work.sort_values(["track_id", "time"], kind="mergesort").groupby("track_id", sort=False):
        valid = group.loc[np.isfinite(group._peak_metric)]
        peak = (valid if len(valid) else group).iloc[int(np.nanargmax(valid._peak_metric.to_numpy())) if len(valid) else 0]
        genesis = group.iloc[0]
        rows.append(
            {
                "track_id": str(track_id),
                "time": peak.time,
                "lon": float(peak.lon),
                "lat": float(peak.lat),
                "genesis_month": int(genesis._native_month),
                "peak_metric": metric,
            }
        )
    return pd.DataFrame.from_records(rows)


def _precipitation_file(data_root: Path, timestamp: pd.Timestamp) -> Path:
    path = data_root / "standard" / "precipitation" / f"{timestamp:%Y%m}.nc"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _open_precipitation(paths: Iterable[Path]) -> xr.DataArray:
    parts: list[xr.DataArray] = []
    for path in dict.fromkeys(Path(value) for value in paths):
        with xr.open_dataset(path) as source:
            if "mtpr" not in source:
                raise ValueError(f"{path} does not contain mtpr")
            parts.append(source.mtpr.astype(np.float32).load())
    combined = xr.concat(parts, dim="time").sortby("time")
    times = pd.DatetimeIndex(pd.to_datetime(combined.time.values))
    _, keep = np.unique(times.view("int64"), return_index=True)
    return combined.isel(time=np.sort(keep))


def precipitation_footprints(frame: pd.DataFrame, data_root: Path, axis: TimeAxis | None) -> dict[str, Any]:
    peaks = _event_peaks(frame, axis)
    samples: dict[str, list[np.ndarray]] = {season: [] for season in SEASONS}
    peaks["analysis_month"] = peaks.time.dt.strftime("%Y%m")
    for _, group in peaks.groupby("analysis_month", sort=True):
        timestamp = pd.Timestamp(group.time.min())
        current = _precipitation_file(data_root, timestamp)
        previous = _precipitation_file(data_root, (timestamp.to_period("M") - 1).start_time)
        precipitation = _open_precipitation((previous, current))
        units = str(precipitation.attrs.get("units", "")).lower().replace(" ", "")
        if "s-1" not in units and "s**-1" not in units:
            raise ValueError(f"{current}: mtpr is not a precipitation flux: {precipitation.attrs.get('units')!r}")
        for row in group.itertuples(index=False):
            end = pd.Timestamp(row.time)
            start = end - pd.Timedelta(hours=23)
            interval = precipitation.sel(time=slice(start, end))
            if interval.sizes.get("time") != 24:
                raise ValueError(f"24-hour footprint for {row.track_id} has {interval.sizes.get('time')} samples")
            field = interval.sum("time", skipna=False) * np.float32(3600.0)
            sampled = field.interp(
                latitude=xr.DataArray(row.lat + FOOTPRINT_OFFSETS, dims="relative_latitude"),
                longitude=xr.DataArray(row.lon + FOOTPRINT_OFFSETS, dims="relative_longitude"),
                method="linear",
            ).transpose("relative_latitude", "relative_longitude")
            array = np.asarray(sampled.values, dtype=np.float32)
            for season, months in SEASONS.items():
                if int(row.genesis_month) in months:
                    samples[season].append(array)
    result: dict[str, Any] = {}
    for season, values in samples.items():
        if not values:
            result[season] = {"samples": 0, "mean_mm": None, "standard_error_mm": None, "valid_counts": None}
            continue
        stack = np.stack(values).astype(np.float64)
        valid = np.isfinite(stack)
        counts = valid.sum(axis=0)
        total = np.nansum(stack, axis=0)
        mean = np.divide(total, counts, out=np.full(total.shape, np.nan), where=counts > 0)
        squared = np.nansum((stack - mean[None, :, :]) ** 2, axis=0)
        variance = np.divide(squared, counts - 1, out=np.full(total.shape, np.nan), where=counts > 1)
        standard_error = np.sqrt(variance / np.maximum(counts, 1))
        result[season] = {
            "samples": len(values),
            "mean_mm": _json_array(mean, 4),
            "standard_error_mm": _json_array(standard_error, 4),
            "valid_counts": counts.astype(int).tolist(),
        }
    return {
        "relative_longitude_deg": FOOTPRINT_OFFSETS.tolist(),
        "relative_latitude_deg": FOOTPRINT_OFFSETS.tolist(),
        "centering": "one trailing-24-hour precipitation field per event, centred on the maximum circulation-wind hour",
        "event_weighting": "one event, one vote",
        "seasons": result,
    }


def india_masks(
    geometry_asset: Path,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, Any]]:
    payload = _load_json(geometry_asset)
    states = payload.get("geo", {}).get("states", [])
    state_polygons: dict[str, list[Any]] = defaultdict(list)
    ring_count = 0
    for state in states:
        for ring in state.get("rings", []):
            if len(ring) < 4:
                continue
            polygon = make_valid(Polygon(ring))
            if not polygon.is_empty:
                state_polygons[str(state["id"])].append(polygon)
                ring_count += 1
    if not state_polygons:
        raise ValueError(f"no state/UT polygons found in {geometry_asset}")
    polygons = [polygon for values in state_polygons.values() for polygon in values]
    geometry = union_all(polygons)
    longitude, latitude = np.meshgrid(TARGET_LONS, TARGET_LATS)
    mask = np.asarray(intersects_xy(geometry, longitude, latitude), dtype=bool)
    if int(mask.sum()) < 100:
        raise ValueError(f"India mask has only {int(mask.sum())} one-degree cells")

    missing = sorted(
        state_id
        for region in INDIA_REGIONS.values()
        for state_id in region["state_ids"]
        if state_id not in state_polygons
    )
    if missing:
        raise ValueError(f"regional India masks refer to missing state/UT geometry: {missing}")
    regional_masks: dict[str, np.ndarray] = {}
    regional_metadata: dict[str, Any] = {}
    for region_id, definition in INDIA_REGIONS.items():
        region_geometry = union_all(
            [
                polygon
                for state_id in definition["state_ids"]
                for polygon in state_polygons[state_id]
            ]
        )
        region_mask = np.asarray(intersects_xy(region_geometry, longitude, latitude), dtype=bool)
        region_mask &= mask
        if int(region_mask.sum()) < 3:
            raise ValueError(f"{region_id} mask has only {int(region_mask.sum())} one-degree cells")
        regional_masks[region_id] = region_mask
        regional_metadata[region_id] = {
            "label": definition["label"],
            "state_ids": list(definition["state_ids"]),
            "grid_cells": int(region_mask.sum()),
        }
    regional_union = np.logical_or.reduce(list(regional_masks.values()))
    return mask, regional_masks, {
        "source": str(geometry_asset),
        "source_sha256": sha256(geometry_asset),
        "state_or_ut_features": len(states),
        "polygon_rings": ring_count,
        "grid_cells": int(mask.sum()),
        "regional_definition": "six fixed mainland macro-regions assembled from the atlas state/UT polygons",
        "regional_grid_cells": int(regional_union.sum()),
        "regional_unassigned_grid_cells": int((mask & ~regional_union).sum()),
        "regions": regional_metadata,
    }


def india_mask(geometry_asset: Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Retain the original national-mask API for downstream callers."""
    mask, _regional_masks, metadata = india_masks(geometry_asset)
    return mask, metadata


def _daily_india_precipitation(
    data_root: Path,
    axis: TimeAxis | None,
    mask: np.ndarray,
    start_year: int,
    end_year: int,
) -> dict[tuple[int, int, int], np.ndarray]:
    totals: dict[tuple[int, int, int], np.ndarray] = {}
    counts: dict[tuple[int, int, int], np.ndarray] = {}
    files = sorted((data_root / "standard" / "precipitation").glob("*.nc"))
    if not files:
        raise FileNotFoundError(data_root / "standard" / "precipitation")
    for path in files:
        with xr.open_dataset(path) as source:
            if "mtpr" not in source:
                raise ValueError(f"{path} does not contain mtpr")
            components = native_components(source.time.values, axis)
            selected = (
                components.year.between(start_year, end_year)
                & components.month.isin(SEASONS["jjas"])
            ).to_numpy()
            if not selected.any():
                continue
            values = np.asarray(source.mtpr.isel(time=np.flatnonzero(selected)).values, dtype=np.float32)
            values = values[:, mask] * np.float32(3600.0)
            selected_components = components.loc[selected].reset_index(drop=True)
            for key, indexes in selected_components.groupby(["year", "month", "day"], sort=False).groups.items():
                chunk = values[np.asarray(list(indexes), dtype=int)]
                valid = np.isfinite(chunk)
                addition = np.nansum(chunk, axis=0, dtype=np.float64)
                number = valid.sum(axis=0).astype(np.int16)
                if key in totals:
                    totals[key] += addition
                    counts[key] += number
                else:
                    totals[key] = addition
                    counts[key] = number
    for key in totals:
        totals[key] = totals[key].astype(np.float32)
        totals[key][counts[key] != 24] = np.nan
    return totals


def _track_native_keys(frame: pd.DataFrame, axis: TimeAxis | None) -> pd.DataFrame:
    work = frame[["track_id", "time", "lon", "lat"]].copy()
    work["time"] = pd.to_datetime(work.time, errors="raise")
    components = track_components(frame.reset_index(drop=True), axis)
    for column in ("year", "month", "day", "hour"):
        work[column] = components[column].to_numpy()
    return work


def _exposure_by_day(
    tracks: pd.DataFrame,
    keys: list[tuple[int, int, int]],
    grid_latitude: np.ndarray,
    grid_longitude: np.ndarray,
) -> dict[tuple[int, int, int], np.ndarray]:
    grid_radians = np.deg2rad(np.column_stack([grid_latitude, grid_longitude]))
    tree = BallTree(grid_radians, metric="haversine")
    radius = EXPOSURE_RADIUS_KM / EARTH_RADIUS_KM
    result = {key: np.zeros(len(grid_latitude), dtype=bool) for key in keys}
    selected = tracks.loc[tracks.month.isin(SEASONS["jjas"])].copy()
    selected["key"] = list(zip(selected.year, selected.month, selected.day))
    for key, group in selected.groupby("key", sort=False):
        if key not in result:
            continue
        centres = np.deg2rad(group[["lat", "lon"]].to_numpy(float))
        neighbours = tree.query_radius(centres, r=radius, return_distance=False)
        nonempty = [value for value in neighbours if len(value)]
        if nonempty:
            result[key][np.unique(np.concatenate(nonempty))] = True
    return result


def _monthly_control_excess(
    rainfall: np.ndarray,
    exposed: np.ndarray,
    months: np.ndarray,
    weights: np.ndarray,
) -> float:
    value = 0.0
    for month in SEASONS["jjas"]:
        selected = months == month
        rain = rainfall[selected]
        exposure = exposed[selected]
        control = ~exposure & np.isfinite(rain)
        total = np.nansum(np.where(control, rain, 0.0), axis=0)
        count = control.sum(axis=0)
        baseline = np.divide(total, count, out=np.full(total.shape, np.nan), where=count > 0)
        anomaly = rain - baseline[None, :]
        valid = exposure & np.isfinite(anomaly)
        value += float(np.nansum(np.where(valid, anomaly * weights[None, :], 0.0)))
    return value


def _rainfall_record(
    rainfall: np.ndarray,
    exposed: np.ndarray,
    months: np.ndarray,
    weights: np.ndarray,
    daily_climatology: np.ndarray,
) -> dict[str, float | None]:
    valid = np.isfinite(rainfall)
    weighted = np.where(valid, rainfall * weights[None, :], 0.0)
    total = float(weighted.sum())
    attributed = float(np.where(exposed, weighted, 0.0).sum())
    valid_weight = float(np.where(valid, weights[None, :], 0.0).sum())
    exposure_weight = float(np.where(exposed & valid, weights[None, :], 0.0).sum())
    anomaly = rainfall - daily_climatology
    climatological_excess = float(
        np.nansum(np.where(exposed, anomaly * weights[None, :], 0.0))
    )
    month_control_excess = _monthly_control_excess(rainfall, exposed, months, weights)
    record: dict[str, float | None] = {
        "rainfall_share": attributed / total if total else None,
        "climatological_excess_share": climatological_excess / total if total else None,
        "month_control_excess_share": month_control_excess / total if total else None,
        "exposed_area_day_fraction": exposure_weight / valid_weight if valid_weight else None,
        "exposed_to_all_rain_ratio": (
            (attributed / exposure_weight) / (total / valid_weight)
            if exposure_weight and total else None
        ),
        "regional_mean_mm_day": total / valid_weight if valid_weight else None,
        "exposed_mean_mm_day": attributed / exposure_weight if exposure_weight else None,
    }
    for threshold in (20.0, 50.0):
        heavy = valid & (rainfall >= threshold)
        denominator = float(np.where(heavy, weights[None, :], 0.0).sum())
        numerator = float(np.where(heavy & exposed, weights[None, :], 0.0).sum())
        record[f"heavy_{int(threshold)}mm_exposed_cell_day_share"] = (
            numerator / denominator if denominator else None
        )
    return record


def _metric_means(records: list[dict[str, Any]], metrics: Iterable[str]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for metric in metrics:
        values = np.asarray(
            [record[metric] for record in records if record.get(metric) is not None],
            dtype=float,
        )
        values = values[np.isfinite(values)]
        result[metric] = float(values.mean()) if len(values) else None
    return result


def india_rainfall_diagnostics(
    frame: pd.DataFrame,
    data_root: Path,
    axis: TimeAxis | None,
    mask: np.ndarray,
    start_year: int,
    end_year: int,
    regional_masks: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    daily = _daily_india_precipitation(data_root, axis, mask, start_year, end_year)
    keys = sorted(daily)
    if not keys:
        raise ValueError("no native-calendar JJAS precipitation days were found")
    expected_years = list(range(start_year, end_year + 1))
    missing_years = sorted(set(expected_years) - {key[0] for key in keys})
    if missing_years:
        raise ValueError(f"JJAS precipitation is missing years: {missing_years[:12]}")
    longitude, latitude = np.meshgrid(TARGET_LONS, TARGET_LATS)
    grid_latitude = latitude[mask]
    grid_longitude = longitude[mask]
    tracks = _track_native_keys(frame, axis)
    exposure = _exposure_by_day(tracks, keys, grid_latitude, grid_longitude)
    weights = np.cos(np.deg2rad(grid_latitude)).astype(np.float64)

    climatology_total: dict[tuple[int, int], np.ndarray] = defaultdict(lambda: np.zeros(len(weights), dtype=np.float64))
    climatology_count: dict[tuple[int, int], np.ndarray] = defaultdict(lambda: np.zeros(len(weights), dtype=np.int32))
    for (year, month, day), values in daily.items():
        key = (month, day)
        valid = np.isfinite(values)
        climatology_total[key][valid] += values[valid]
        climatology_count[key][valid] += 1
    climatology = {
        key: np.divide(total, climatology_count[key], out=np.full(total.shape, np.nan), where=climatology_count[key] > 0)
        for key, total in climatology_total.items()
    }

    records: list[dict[str, Any]] = []
    regional_records: dict[str, list[dict[str, Any]]] = {
        region_id: [] for region_id in (regional_masks or {})
    }
    regional_indexes = {
        region_id: np.asarray(region_mask[mask], dtype=bool)
        for region_id, region_mask in (regional_masks or {}).items()
    }
    genesis = tracks.sort_values(["track_id", "time"], kind="mergesort").groupby("track_id", sort=False).first()
    for year in expected_years:
        year_keys = [key for key in keys if key[0] == year]
        rainfall = np.stack([daily[key] for key in year_keys]).astype(np.float64)
        exposed = np.stack([exposure[key] for key in year_keys])
        months = np.asarray([key[1] for key in year_keys], dtype=int)
        daily_climatology = np.stack([climatology[(month, day)] for _, month, day in year_keys])
        active_ids = tracks.loc[
            tracks.year.eq(year) & tracks.month.isin(SEASONS["jjas"]), "track_id"
        ].nunique()
        genesis_ids = genesis.loc[
            genesis.year.eq(year) & genesis.month.isin(SEASONS["jjas"])
        ].index.nunique()
        national = _rainfall_record(rainfall, exposed, months, weights, daily_climatology)
        all_india_mean = national.pop("regional_mean_mm_day")
        record: dict[str, Any] = {
            "year": year,
            "days": len(year_keys),
            **national,
            "all_india_mean_mm_day": all_india_mean,
            "active_lps": int(active_ids),
            "genesis_lps": int(genesis_ids),
        }
        records.append(record)
        for region_id, selected in regional_indexes.items():
            regional_records[region_id].append(
                {
                    "year": year,
                    "days": len(year_keys),
                    **_rainfall_record(
                        rainfall[:, selected],
                        exposed[:, selected],
                        months,
                        weights[selected],
                        daily_climatology[:, selected],
                    ),
                }
            )
    summary = _metric_means(records, INDIA_METRICS)
    regions = {
        region_id: {
            "label": INDIA_REGIONS[region_id]["label"],
            "state_ids": list(INDIA_REGIONS[region_id]["state_ids"]),
            "grid_cells": int(regional_indexes[region_id].sum()),
            "years": values,
            "mean": _metric_means(values, REGIONAL_RAIN_METRICS),
        }
        for region_id, values in regional_records.items()
    }
    return {
        "radius_km": EXPOSURE_RADIUS_KM,
        "season": "native-calendar JJAS",
        "exposure_rule": "An Indian 1-degree grid cell is exposed on a model-calendar day when any hourly LPS centre lies within 800 km.",
        "weighting": "cosine-latitude area weights; overlapping systems do not double count a cell-day",
        "climatology": "within-run 30-year model-calendar day climatology",
        "years": records,
        "mean": summary,
        "regions": regions,
    }


def build_run(
    period_plan: Path,
    catalogue: Path,
    data_root: Path,
    geometry_asset: Path,
    output_dir: Path,
) -> Path:
    plan = json.loads(period_plan.read_text(encoding="utf-8"))
    frame = pd.read_parquet(catalogue)
    frame["time"] = pd.to_datetime(frame.time, errors="raise")
    axis = _axis(plan)
    mask, regional_masks, geometry = india_masks(geometry_asset)
    start_year, end_year = int(plan["core_start"][:4]), int(plan["core_end"][:4])
    payload = {
        "schema": RUN_SCHEMA,
        "generated_utc": utc_now(),
        "run": plan["run"],
        "coverage": {"start_year": start_year, "end_year": end_year, "years": end_year - start_year + 1},
        "storm_centred_precipitation": precipitation_footprints(frame, data_root, axis),
        "india_geometry": geometry,
        "india_jjas_rainfall": india_rainfall_diagnostics(
            frame, data_root, axis, mask, start_year, end_year, regional_masks
        ),
        "provenance": {
            "catalogue": {"path": str(catalogue), "sha256": sha256(catalogue)},
            "period_plan": {"path": str(period_plan), "sha256": sha256(period_plan)},
            "standard_precipitation_root": str(data_root / "standard" / "precipitation"),
        },
    }
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    asset = output_dir / f"climate-impact.{digest[:12]}.json.gz"
    atomic_gzip_json(asset, payload)
    _atomic_json(
        output_dir / "manifest.json",
        {
            "schema": RUN_SCHEMA,
            "generated_utc": payload["generated_utc"],
            "run": plan["run"],
            "asset": {"path": asset.name, "sha256": sha256(asset), "bytes": asset.stat().st_size},
        },
    )
    return asset


def build_pair(historical_manifest: Path, future_manifest: Path, output_dir: Path) -> Path:
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in (historical_manifest, future_manifest)]
    payloads = []
    for path, manifest in zip((historical_manifest, future_manifest), manifests, strict=True):
        asset = Path(manifest["asset"]["path"])
        if not asset.is_absolute():
            asset = path.parent / asset
        if sha256(asset) != manifest["asset"]["sha256"]:
            raise ValueError(f"impact asset checksum does not match {path}")
        payloads.append(_load_json(asset))
    historical, future = payloads
    metrics: dict[str, Any] = {}
    for index, metric in enumerate(INDIA_METRICS):
        left = np.asarray([record.get(metric, np.nan) for record in historical["india_jjas_rainfall"]["years"]], dtype=float)
        right = np.asarray([record.get(metric, np.nan) for record in future["india_jjas_rainfall"]["years"]], dtype=float)
        left = left[np.isfinite(left)]
        right = right[np.isfinite(right)]
        if not len(left) or not len(right):
            metrics[metric] = None
            continue
        metrics[metric] = bootstrap_change(left, right, seed=1901 + index)
    historical_regions = historical["india_jjas_rainfall"].get("regions", {})
    future_regions = future["india_jjas_rainfall"].get("regions", {})
    if set(historical_regions) != set(future_regions):
        raise ValueError("historical and future regional India masks do not match")
    regional_changes: dict[str, Any] = {}
    for region_index, (region_id, historical_region) in enumerate(historical_regions.items()):
        future_region = future_regions[region_id]
        if historical_region["state_ids"] != future_region["state_ids"]:
            raise ValueError(f"historical and future definitions differ for {region_id}")
        changes: dict[str, Any] = {}
        for metric_index, metric in enumerate(REGIONAL_RAIN_METRICS):
            left = np.asarray(
                [record.get(metric, np.nan) for record in historical_region["years"]],
                dtype=float,
            )
            right = np.asarray(
                [record.get(metric, np.nan) for record in future_region["years"]],
                dtype=float,
            )
            left, right = left[np.isfinite(left)], right[np.isfinite(right)]
            changes[metric] = (
                bootstrap_change(
                    left,
                    right,
                    seed=2901 + region_index * 100 + metric_index,
                )
                if len(left) and len(right) else None
            )
        regional_changes[region_id] = {
            "label": historical_region["label"],
            "state_ids": historical_region["state_ids"],
            "grid_cells": historical_region["grid_cells"],
            "changes": changes,
        }
    footprints: dict[str, Any] = {}
    for season in SEASONS:
        left = historical["storm_centred_precipitation"]["seasons"][season]
        right = future["storm_centred_precipitation"]["seasons"][season]
        left_mean = np.asarray(left["mean_mm"], dtype=float) if left["mean_mm"] is not None else None
        right_mean = np.asarray(right["mean_mm"], dtype=float) if right["mean_mm"] is not None else None
        if left_mean is None or right_mean is None:
            footprints[season] = {"historical_samples": left["samples"], "future_samples": right["samples"], "change_mm": None, "percent_change": None}
            continue
        difference = right_mean - left_mean
        percentage = np.divide(
            difference * 100.0,
            left_mean,
            out=np.full(left_mean.shape, np.nan),
            where=np.abs(left_mean) >= 0.1,
        )
        footprints[season] = {
            "historical_samples": left["samples"],
            "future_samples": right["samples"],
            "historical_mean_mm": _json_array(left_mean, 4),
            "future_mean_mm": _json_array(right_mean, 4),
            "change_mm": _json_array(difference, 4),
            "percent_change": _json_array(percentage, 2),
        }
    payload = {
        "schema": PAIR_SCHEMA,
        "generated_utc": utc_now(),
        "historical": historical["run"],
        "future": future["run"],
        "india_jjas_changes": metrics,
        "regional_india_jjas_changes": regional_changes,
        "storm_centred_precipitation": {
            "relative_longitude_deg": historical["storm_centred_precipitation"]["relative_longitude_deg"],
            "relative_latitude_deg": historical["storm_centred_precipitation"]["relative_latitude_deg"],
            "seasons": footprints,
        },
        "uncertainty": "5th--95th percentile of 5,000 independent year-resampling bootstrap differences",
    }
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    asset = output_dir / f"climate-impact-pair.{digest[:12]}.json.gz"
    atomic_gzip_json(asset, payload)
    _atomic_json(
        output_dir / "manifest.json",
        {
            "schema": PAIR_SCHEMA,
            "generated_utc": payload["generated_utc"],
            "asset": {"path": asset.name, "sha256": sha256(asset), "bytes": asset.stat().st_size},
            "historical_manifest": str(historical_manifest.resolve()),
            "future_manifest": str(future_manifest.resolve()),
        },
    )
    return asset


def build_task_plan(run_root: Path, pair_roots: list[Path], geometry_asset: Path) -> Path:
    run_root = run_root.resolve()
    geometry_asset = geometry_asset.resolve()
    if not geometry_asset.is_file():
        raise FileNotFoundError(geometry_asset)
    run_rows: list[list[object]] = []
    pair_rows: list[list[object]] = []
    records: list[dict[str, Any]] = []
    for pair_root in pair_roots:
        pair_root = pair_root.resolve()
        plans = sorted(pair_root.glob("*/period-plan.json"))
        by_experiment: dict[str, Path] = {}
        for plan_path in plans:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            experiment = str(plan["run"]["experiment_id"])
            if experiment in by_experiment:
                raise ValueError(f"duplicate {experiment} plan below {pair_root}")
            by_experiment[experiment] = plan_path
        if "historical" not in by_experiment or len(by_experiment) != 2:
            raise ValueError(f"{pair_root} must contain one historical and one future period")
        future_experiment = next(value for value in by_experiment if value != "historical")
        manifests: dict[str, Path] = {}
        pair_record = {"pair_root": str(pair_root), "future_experiment": future_experiment, "runs": {}}
        for experiment in ("historical", future_experiment):
            plan_path = by_experiment[experiment]
            period_root = plan_path.parent
            catalogue = period_root / "physics" / "cmip6-physical-events.parquet"
            data_root = period_root / "data"
            output_dir = period_root / "impact"
            if not catalogue.is_file():
                raise FileNotFoundError(catalogue)
            if not (data_root / "standard" / "precipitation").is_dir():
                raise FileNotFoundError(data_root / "standard" / "precipitation")
            run_rows.append(
                [len(run_rows) + 1, plan_path, catalogue, data_root, geometry_asset, output_dir]
            )
            manifests[experiment] = output_dir / "manifest.json"
            pair_record["runs"][experiment] = {
                "period_plan": str(plan_path),
                "catalogue": str(catalogue),
                "output": str(output_dir),
            }
        pair_output = pair_root / "climate-impact"
        pair_rows.append(
            [len(pair_rows) + 1, manifests["historical"], manifests[future_experiment], pair_output]
        )
        pair_record["output"] = str(pair_output)
        records.append(pair_record)
    _atomic_tsv(run_root / "run.tsv", run_rows)
    _atomic_tsv(run_root / "pair.tsv", pair_rows)
    manifest = run_root / "plan.json"
    _atomic_json(
        manifest,
        {
            "schema": "lps-atlas-cmip6-precipitation-impact-plan-v1",
            "generated_utc": utc_now(),
            "geometry_asset": {"path": str(geometry_asset), "sha256": sha256(geometry_asset)},
            "runs": len(run_rows),
            "pairs": len(pair_rows),
            "records": records,
        },
    )
    return manifest


def _manifest_payload(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = Path(manifest["asset"]["path"])
    if not asset.is_absolute():
        asset = manifest_path.resolve().parent / asset
    if sha256(asset) != manifest["asset"]["sha256"]:
        raise ValueError(f"asset checksum does not match {manifest_path}")
    return manifest, _load_json(asset), asset


def _run_payload(manifest_path: Path) -> dict[str, Any]:
    _, payload, _ = _manifest_payload(manifest_path)
    if payload.get("schema") != RUN_SCHEMA:
        raise ValueError(f"unsupported impact run schema in {manifest_path}")
    return payload


def aggregate_impact_payloads(
    entries: list[dict[str, Any]],
    *,
    samples: int = 5000,
) -> dict[str, Any]:
    if len(entries) < 2:
        raise ValueError("impact ensemble requires at least two models")
    changes: dict[str, Any] = {}
    for metric_index, metric in enumerate(INDIA_METRICS):
        model_records: list[dict[str, Any]] = []
        historical_draws: list[np.ndarray] = []
        future_draws: list[np.ndarray] = []
        rng = np.random.default_rng(8421 + metric_index)
        for entry in entries:
            change = entry["pair"]["india_jjas_changes"].get(metric)
            if change is None:
                continue
            left = np.asarray(
                [record.get(metric, np.nan) for record in entry["historical"]["india_jjas_rainfall"]["years"]],
                dtype=float,
            )
            right = np.asarray(
                [record.get(metric, np.nan) for record in entry["future"]["india_jjas_rainfall"]["years"]],
                dtype=float,
            )
            left, right = left[np.isfinite(left)], right[np.isfinite(right)]
            if not len(left) or not len(right):
                continue
            historical_draws.append(left[rng.integers(0, len(left), size=(samples, len(left)))].mean(axis=1))
            future_draws.append(right[rng.integers(0, len(right), size=(samples, len(right)))].mean(axis=1))
            model_records.append(
                {
                    "id": entry["id"],
                    "source_label": entry["source_label"],
                    **change,
                }
            )
        if not model_records:
            changes[metric] = None
            continue
        historical = float(np.mean([record["historical"] for record in model_records]))
        future = float(np.mean([record["future"] for record in model_records]))
        historical_bootstrap = np.mean(np.stack(historical_draws), axis=0)
        future_bootstrap = np.mean(np.stack(future_draws), axis=0)
        difference = future_bootstrap - historical_bootstrap
        percentage = np.divide(
            difference * 100.0,
            historical_bootstrap,
            out=np.full(difference.shape, np.nan),
            where=historical_bootstrap != 0,
        )
        percentage = percentage[np.isfinite(percentage)]
        changes[metric] = {
            "historical": historical,
            "future": future,
            "absolute_change": future - historical,
            "percent_change": (future / historical - 1.0) * 100.0 if historical else None,
            "ci05": float(np.quantile(difference, .05)),
            "ci95": float(np.quantile(difference, .95)),
            "percent_ci05": float(np.quantile(percentage, .05)) if len(percentage) else None,
            "percent_ci95": float(np.quantile(percentage, .95)) if len(percentage) else None,
            "model_count": len(model_records),
            "models": model_records,
        }

    available_region_sets = [
        set(entry["pair"].get("regional_india_jjas_changes", {})) for entry in entries
    ]
    if any(available_region_sets) and any(
        region_set != available_region_sets[0] for region_set in available_region_sets[1:]
    ):
        raise ValueError("impact pairs do not use the same regional India definitions")
    regional_changes: dict[str, Any] = {}
    region_ids = list(entries[0]["pair"].get("regional_india_jjas_changes", {}))
    for region_index, region_id in enumerate(region_ids):
        first_region = entries[0]["pair"]["regional_india_jjas_changes"][region_id]
        metric_changes: dict[str, Any] = {}
        for metric_index, metric in enumerate(REGIONAL_RAIN_METRICS):
            model_records: list[dict[str, Any]] = []
            historical_draws: list[np.ndarray] = []
            future_draws: list[np.ndarray] = []
            rng = np.random.default_rng(9421 + region_index * 100 + metric_index)
            for entry in entries:
                pair_region = entry["pair"]["regional_india_jjas_changes"][region_id]
                change = pair_region["changes"].get(metric)
                if change is None:
                    continue
                historical_region = entry["historical"]["india_jjas_rainfall"]["regions"][region_id]
                future_region = entry["future"]["india_jjas_rainfall"]["regions"][region_id]
                left = np.asarray(
                    [record.get(metric, np.nan) for record in historical_region["years"]],
                    dtype=float,
                )
                right = np.asarray(
                    [record.get(metric, np.nan) for record in future_region["years"]],
                    dtype=float,
                )
                left, right = left[np.isfinite(left)], right[np.isfinite(right)]
                if not len(left) or not len(right):
                    continue
                historical_draws.append(
                    left[rng.integers(0, len(left), size=(samples, len(left)))].mean(axis=1)
                )
                future_draws.append(
                    right[rng.integers(0, len(right), size=(samples, len(right)))].mean(axis=1)
                )
                model_records.append(
                    {"id": entry["id"], "source_label": entry["source_label"], **change}
                )
            if not model_records:
                metric_changes[metric] = None
                continue
            historical_mean = float(np.mean([record["historical"] for record in model_records]))
            future_mean = float(np.mean([record["future"] for record in model_records]))
            historical_bootstrap = np.mean(np.stack(historical_draws), axis=0)
            future_bootstrap = np.mean(np.stack(future_draws), axis=0)
            difference = future_bootstrap - historical_bootstrap
            percentage = np.divide(
                difference * 100.0,
                historical_bootstrap,
                out=np.full(difference.shape, np.nan),
                where=historical_bootstrap != 0,
            )
            percentage = percentage[np.isfinite(percentage)]
            metric_changes[metric] = {
                "historical": historical_mean,
                "future": future_mean,
                "absolute_change": future_mean - historical_mean,
                "percent_change": (
                    (future_mean / historical_mean - 1.0) * 100.0
                    if historical_mean else None
                ),
                "ci05": float(np.quantile(difference, .05)),
                "ci95": float(np.quantile(difference, .95)),
                "percent_ci05": float(np.quantile(percentage, .05)) if len(percentage) else None,
                "percent_ci95": float(np.quantile(percentage, .95)) if len(percentage) else None,
                "model_count": len(model_records),
                "models": model_records,
            }
        regional_changes[region_id] = {
            "label": first_region["label"],
            "state_ids": first_region["state_ids"],
            "grid_cells": first_region["grid_cells"],
            "changes": metric_changes,
        }

    footprints: dict[str, Any] = {}
    for season in SEASONS:
        historical_maps, future_maps, model_records = [], [], []
        for entry in entries:
            footprint = entry["pair"]["storm_centred_precipitation"]["seasons"][season]
            if footprint.get("historical_mean_mm") is None or footprint.get("future_mean_mm") is None:
                continue
            left = np.asarray(footprint["historical_mean_mm"], dtype=float)
            right = np.asarray(footprint["future_mean_mm"], dtype=float)
            historical_maps.append(left)
            future_maps.append(right)
            model_records.append(
                {
                    "id": entry["id"],
                    "source_label": entry["source_label"],
                    "historical_samples": footprint["historical_samples"],
                    "future_samples": footprint["future_samples"],
                    "domain_mean_percent_change": float((np.nanmean(right) / np.nanmean(left) - 1.0) * 100.0),
                }
            )
        if not historical_maps:
            footprints[season] = {"model_count": 0, "historical_mean_mm": None, "future_mean_mm": None, "change_mm": None, "models": []}
            continue
        historical = np.nanmean(np.stack(historical_maps), axis=0)
        future = np.nanmean(np.stack(future_maps), axis=0)
        footprints[season] = {
            "model_count": len(historical_maps),
            "historical_mean_mm": _json_array(historical, 4),
            "future_mean_mm": _json_array(future, 4),
            "change_mm": _json_array(future - historical, 4),
            "models": model_records,
        }
    first = entries[0]["pair"]["storm_centred_precipitation"]
    return {
        "schema": ENSEMBLE_SCHEMA,
        "generated_utc": utc_now(),
        "aggregation": "one_model_one_vote",
        "model_count": len(entries),
        "model_ids": [entry["id"] for entry in entries],
        "india_jjas_changes": changes,
        "regional_india_jjas_changes": regional_changes,
        "storm_centred_precipitation": {
            "relative_longitude_deg": first["relative_longitude_deg"],
            "relative_latitude_deg": first["relative_latitude_deg"],
            "seasons": footprints,
        },
        "uncertainty": "5th--95th percentile of 5,000 within-model year-resampling draws with one model, one vote",
    }


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def attach_to_climate_bundle(climate_manifest: Path, impact_manifests: list[Path]) -> Path:
    climate_manifest = climate_manifest.resolve()
    output_dir = climate_manifest.parent
    manifest = json.loads(climate_manifest.read_text(encoding="utf-8"))
    index_path = output_dir / manifest["index"]["path"]
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError(f"climate index checksum does not match {climate_manifest}")
    index = _load_json(index_path)
    pair_records = [pair for pair in index["pairs"] if pair.get("kind") != "multi-model"]
    pairs_by_identity = {
        (
            pair["source_label"],
            pair["member_id"],
            pair["historical"]["run"]["experiment_id"],
            pair["future"]["run"]["experiment_id"],
        ): pair
        for pair in pair_records
    }
    entries: list[dict[str, Any]] = []
    assets_dir = output_dir / "assets"
    for impact_manifest in impact_manifests:
        impact_meta, impact_payload, impact_asset = _manifest_payload(impact_manifest)
        if impact_payload.get("schema") != PAIR_SCHEMA:
            raise ValueError(f"unsupported impact pair schema in {impact_manifest}")
        identity = (
            impact_payload["historical"]["source_id"],
            impact_payload["historical"]["member_id"],
            impact_payload["historical"]["experiment_id"],
            impact_payload["future"]["experiment_id"],
        )
        if identity not in pairs_by_identity:
            raise ValueError(f"impact pair {identity} is absent from the climate bundle")
        pair = pairs_by_identity[identity]
        destination = assets_dir / impact_asset.name
        _copy_atomic(impact_asset, destination)
        pair["impact"] = {
            "url": f"assets/{destination.name}",
            "sha256": impact_meta["asset"]["sha256"],
            "bytes": destination.stat().st_size,
        }
        historical_path = Path(impact_meta["historical_manifest"])
        future_path = Path(impact_meta["future_manifest"])
        entries.append(
            {
                "id": pair["id"],
                "source_label": pair["source_label"],
                "pair": impact_payload,
                "historical": _run_payload(historical_path),
                "future": _run_payload(future_path),
            }
        )
    if len(entries) != len(pair_records):
        raise ValueError(f"received {len(entries)} impact pairs for {len(pair_records)} climate pairs")
    order = {pair["id"]: position for position, pair in enumerate(pair_records)}
    entries.sort(key=lambda entry: order[entry["id"]])
    ensemble_payload = aggregate_impact_payloads(entries)
    raw_ensemble = json.dumps(ensemble_payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ensemble_path = assets_dir / f"climate-impact-ensemble.{hashlib.sha256(raw_ensemble).hexdigest()[:12]}.json.gz"
    atomic_gzip_json(ensemble_path, ensemble_payload)
    multi = next((pair for pair in index["pairs"] if pair.get("kind") == "multi-model"), None)
    if multi is None:
        raise ValueError("climate bundle has no multi-model record")
    multi["impact"] = {
        "url": f"assets/{ensemble_path.name}",
        "sha256": sha256(ensemble_path),
        "bytes": ensemble_path.stat().st_size,
    }
    index["generated_utc"] = utc_now()
    raw_index = json.dumps(index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    new_index = output_dir / f"climate-index.{hashlib.sha256(raw_index).hexdigest()[:12]}.json.gz"
    atomic_gzip_json(new_index, index)
    manifest.update(
        {
            "generated_utc": index["generated_utc"],
            "index": {"path": new_index.name, "sha256": sha256(new_index), "bytes": new_index.stat().st_size},
            "impact_models": len(entries),
            "impact_schema": ENSEMBLE_SCHEMA,
        }
    )
    _atomic_json(climate_manifest, manifest)
    return new_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--run-root", type=Path, required=True)
    plan.add_argument("--pair-root", type=Path, action="append", required=True)
    plan.add_argument("--geometry-asset", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--period-plan", type=Path, required=True)
    run.add_argument("--catalogue", type=Path, required=True)
    run.add_argument("--data-root", type=Path, required=True)
    run.add_argument("--geometry-asset", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    pair = subparsers.add_parser("pair")
    pair.add_argument("--historical-manifest", type=Path, required=True)
    pair.add_argument("--future-manifest", type=Path, required=True)
    pair.add_argument("--output-dir", type=Path, required=True)
    attach = subparsers.add_parser("attach")
    attach.add_argument("--climate-manifest", type=Path, required=True)
    attach.add_argument("--impact-manifest", type=Path, action="append", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "plan":
        result = build_task_plan(args.run_root, args.pair_root, args.geometry_asset)
    elif args.command == "run":
        result = build_run(args.period_plan, args.catalogue, args.data_root, args.geometry_asset, args.output_dir)
    elif args.command == "pair":
        result = build_pair(args.historical_manifest, args.future_manifest, args.output_dir)
    else:
        result = attach_to_climate_bundle(args.climate_manifest, args.impact_manifest)
    print(result)


if __name__ == "__main__":
    main()
