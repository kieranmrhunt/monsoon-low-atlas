#!/usr/bin/env python3
"""Build compact per-run and paired climate-change assets from CMIP6 LPS tracks."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reanalysis_pipeline.common import sha256


SCHEMA = "lps-atlas-cmip6-climate-summary-v2"
PAIR_SCHEMA = "lps-atlas-cmip6-paired-change-v2"
ENSEMBLE_SCHEMA = "lps-atlas-cmip6-multimodel-change-v2"
INDEX_SCHEMA = "lps-atlas-cmip6-climate-index-v1"
REVIEW_SCHEMA = "lps-atlas-cmip6-ensemble-review-v1"
LAT_EDGES = np.arange(-15.0, 46.0, 1.0)
LON_EDGES = np.arange(45.0, 121.0, 1.0)
SEASONS = {
    "all": tuple(range(1, 13)),
    "jjas": (6, 7, 8, 9),
    "mam": (3, 4, 5),
    "ond": (10, 11, 12),
    "djf": (12, 1, 2),
}
METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    # Counts and classes. The class metrics are deliberately labelled as
    # resolution-sensitive in the browser contract: identical thresholds do
    # not make coarse and fine atmospheric grids equally able to resolve a
    # compact circulation.
    "systems": {
        "label": "Systems",
        "group": "Frequency and class",
        "unit": "yr⁻¹",
        "digits": 1,
        "zero": True,
        "description": "Number of systems grouped by genesis year and season.",
    },
    "depressions_or_stronger": {
        "label": "Depressions or stronger",
        "group": "Frequency and class",
        "unit": "yr⁻¹",
        "digits": 1,
        "zero": True,
        "resolution_sensitive": True,
        "description": "Systems reaching atlas-derived IMD category D or stronger.",
    },
    "deep_depressions_or_stronger": {
        "label": "Deep depressions or stronger",
        "group": "Frequency and class",
        "unit": "yr⁻¹",
        "digits": 1,
        "zero": True,
        "resolution_sensitive": True,
        "description": "Systems reaching atlas-derived IMD category DD or stronger.",
    },
    "cyclonic_storms_or_stronger": {
        "label": "Cyclonic storms or stronger",
        "group": "Frequency and class",
        "unit": "yr⁻¹",
        "digits": 1,
        "zero": True,
        "resolution_sensitive": True,
        "description": "Systems reaching atlas-derived IMD category CS or stronger.",
    },
    "system_days": {
        "label": "System-days",
        "group": "Frequency and class",
        "unit": "days yr⁻¹",
        "digits": 1,
        "zero": True,
        "description": "Sum of published hourly system positions divided by 24.",
    },
    # Lifecycle and displacement.
    "mean_duration_hours": {"label": "Mean duration", "group": "Lifecycle and movement", "unit": "h", "digits": 0, "event": "duration_hours", "reducer": "mean"},
    "median_duration_hours": {"label": "Median duration", "group": "Lifecycle and movement", "unit": "h", "digits": 0, "event": "duration_hours", "reducer": "median"},
    "mean_path_length_km": {"label": "Mean path length", "group": "Lifecycle and movement", "unit": "km", "digits": 0, "event": "path_length_km", "reducer": "mean"},
    "median_path_length_km": {"label": "Median path length", "group": "Lifecycle and movement", "unit": "km", "digits": 0, "event": "path_length_km", "reducer": "median"},
    "mean_translation_speed_kmh": {"label": "Mean translation speed", "group": "Lifecycle and movement", "unit": "km h⁻¹", "digits": 1, "event": "translation_speed_kmh", "reducer": "mean"},
    "mean_westward_displacement_deg": {"label": "Mean westward displacement", "group": "Lifecycle and movement", "unit": "°", "digits": 1, "event": "westward_displacement_deg", "reducer": "mean"},
    "mean_northward_displacement_deg": {"label": "Mean northward displacement", "group": "Lifecycle and movement", "unit": "°", "digits": 1, "event": "northward_displacement_deg", "reducer": "mean"},
    "mean_genesis_lon": {"label": "Mean genesis longitude", "group": "Lifecycle and movement", "unit": "°E", "digits": 1, "event": "genesis_lon", "reducer": "mean"},
    "mean_genesis_lat": {"label": "Mean genesis latitude", "group": "Lifecycle and movement", "unit": "°N", "digits": 1, "event": "genesis_lat", "reducer": "mean"},
    "mean_lysis_lon": {"label": "Mean lysis longitude", "group": "Lifecycle and movement", "unit": "°E", "digits": 1, "event": "lysis_lon", "reducer": "mean"},
    "mean_lysis_lat": {"label": "Mean lysis latitude", "group": "Lifecycle and movement", "unit": "°N", "digits": 1, "event": "lysis_lat", "reducer": "mean"},
    # Wind, pressure and circulation structure.
    "mean_peak_wind_ms": {"label": "Mean peak circulation wind", "group": "Wind and pressure", "unit": "m s⁻¹", "digits": 1, "event": "peak_wind_ms", "reducer": "mean", "resolution_sensitive": True},
    "p90_peak_wind_ms": {"label": "90th percentile peak circulation wind", "group": "Wind and pressure", "unit": "m s⁻¹", "digits": 1, "event": "peak_wind_ms", "reducer": "p90", "resolution_sensitive": True},
    "mean_peak_max_wind_ms": {"label": "Mean peak maximum 10 m wind", "group": "Wind and pressure", "unit": "m s⁻¹", "digits": 1, "event": "peak_max_wind_ms", "reducer": "mean", "resolution_sensitive": True},
    "mean_lifetime_wind_ms": {"label": "Mean lifetime 10 m wind", "group": "Wind and pressure", "unit": "m s⁻¹", "digits": 1, "event": "lifetime_mean_wind_ms", "reducer": "mean"},
    "mean_peak_pressure_deficit_hpa": {"label": "Mean peak pressure deficit", "group": "Wind and pressure", "unit": "hPa", "digits": 1, "event": "peak_pressure_deficit_hpa", "reducer": "mean", "resolution_sensitive": True},
    "p90_peak_pressure_deficit_hpa": {"label": "90th percentile peak pressure deficit", "group": "Wind and pressure", "unit": "hPa", "digits": 1, "event": "peak_pressure_deficit_hpa", "reducer": "p90", "resolution_sensitive": True},
    "mean_minimum_mslp_hpa": {"label": "Mean event minimum MSLP", "group": "Wind and pressure", "unit": "hPa", "digits": 1, "event": "minimum_mslp_hpa", "reducer": "mean", "resolution_sensitive": True},
    "mean_environmental_mslp_hpa": {"label": "Mean environmental MSLP", "group": "Wind and pressure", "unit": "hPa", "digits": 1, "event": "lifetime_environmental_mslp_hpa", "reducer": "mean"},
    "mean_peak_closed_isobars": {"label": "Mean peak closed 2 hPa isobars", "group": "Wind and pressure", "unit": "count", "digits": 1, "event": "peak_closed_isobars", "reducer": "mean", "resolution_sensitive": True},
    "mean_peak_vorticity_x1e5_s1": {"label": "Mean peak smoothed vorticity", "group": "Vorticity structure", "unit": "10⁻⁵ s⁻¹", "digits": 1, "event": "peak_vorticity_x1e5_s1", "reducer": "mean", "resolution_sensitive": True},
    "p90_peak_vorticity_x1e5_s1": {"label": "90th percentile peak smoothed vorticity", "group": "Vorticity structure", "unit": "10⁻⁵ s⁻¹", "digits": 1, "event": "peak_vorticity_x1e5_s1", "reducer": "p90", "resolution_sensitive": True},
    "mean_lifetime_vorticity_850_x1e5_s1": {"label": "Mean lifetime 850 hPa vorticity", "group": "Vorticity structure", "unit": "10⁻⁵ s⁻¹", "digits": 1, "event": "lifetime_vorticity_850_x1e5_s1", "reducer": "mean"},
    "mean_lifetime_vorticity_700_x1e5_s1": {"label": "Mean lifetime 700 hPa vorticity", "group": "Vorticity structure", "unit": "10⁻⁵ s⁻¹", "digits": 1, "event": "lifetime_vorticity_700_x1e5_s1", "reducer": "mean"},
    "mean_lifetime_vorticity_500_x1e5_s1": {"label": "Mean lifetime 500 hPa vorticity", "group": "Vorticity structure", "unit": "10⁻⁵ s⁻¹", "digits": 1, "event": "lifetime_vorticity_500_x1e5_s1", "reducer": "mean"},
    "mean_lifetime_vorticity_deep_x1e5_s1": {"label": "Mean lifetime deep-layer vorticity", "group": "Vorticity structure", "unit": "10⁻⁵ s⁻¹", "digits": 1, "event": "lifetime_vorticity_deep_x1e5_s1", "reducer": "mean"},
    "mean_vorticity_500_to_850_ratio": {"label": "Mean 500 / 850 hPa vorticity ratio", "group": "Vorticity structure", "unit": "ratio", "digits": 2, "event": "lifetime_vorticity_500_to_850_ratio", "reducer": "mean"},
    # Track-centred rain diagnostics.
    "mean_peak_24h_precipitation_mm": {"label": "Mean peak 24 h precipitation", "group": "Precipitation", "unit": "mm", "digits": 1, "event": "peak_24h_precipitation_mm", "reducer": "mean"},
    "p90_peak_24h_precipitation_mm": {"label": "90th percentile peak 24 h precipitation", "group": "Precipitation", "unit": "mm", "digits": 1, "event": "peak_24h_precipitation_mm", "reducer": "p90"},
    "mean_lifetime_24h_precipitation_mm": {"label": "Mean lifetime 24 h precipitation", "group": "Precipitation", "unit": "mm", "digits": 1, "event": "mean_24h_precipitation_mm", "reducer": "mean"},
    "mean_peak_1h_precipitation_mm": {"label": "Mean peak hourly precipitation", "group": "Precipitation", "unit": "mm h⁻¹", "digits": 2, "event": "peak_1h_precipitation_mm", "reducer": "mean"},
    "p90_peak_1h_precipitation_mm": {"label": "90th percentile peak hourly precipitation", "group": "Precipitation", "unit": "mm h⁻¹", "digits": 2, "event": "peak_1h_precipitation_mm", "reducer": "p90"},
    # Moisture, temperature and environmental context. Values are lifecycle
    # means within each event before events are given equal weight by year.
    "mean_q850_gkg": {"label": "Mean 850 hPa specific humidity", "group": "Moisture", "unit": "g kg⁻¹", "digits": 1, "event": "lifetime_q850_gkg", "reducer": "mean"},
    "mean_q700_gkg": {"label": "Mean 700 hPa specific humidity", "group": "Moisture", "unit": "g kg⁻¹", "digits": 1, "event": "lifetime_q700_gkg", "reducer": "mean"},
    "mean_q500_gkg": {"label": "Mean 500 hPa specific humidity", "group": "Moisture", "unit": "g kg⁻¹", "digits": 1, "event": "lifetime_q500_gkg", "reducer": "mean"},
    "mean_q_deep_gkg": {"label": "Mean deep-layer specific humidity", "group": "Moisture", "unit": "g kg⁻¹", "digits": 1, "event": "lifetime_q_deep_gkg", "reducer": "mean"},
    "mean_rh850_pct": {"label": "Mean 850 hPa relative humidity", "group": "Moisture", "unit": "%", "digits": 1, "event": "lifetime_rh850_pct", "reducer": "mean"},
    "mean_rh700_pct": {"label": "Mean 700 hPa relative humidity", "group": "Moisture", "unit": "%", "digits": 1, "event": "lifetime_rh700_pct", "reducer": "mean"},
    "mean_rh500_pct": {"label": "Mean 500 hPa relative humidity", "group": "Moisture", "unit": "%", "digits": 1, "event": "lifetime_rh500_pct", "reducer": "mean"},
    "mean_rh_deep_pct": {"label": "Mean deep-layer relative humidity", "group": "Moisture", "unit": "%", "digits": 1, "event": "lifetime_rh_deep_pct", "reducer": "mean"},
    "mean_t850_k": {"label": "Mean 850 hPa temperature", "group": "Temperature and core", "unit": "K", "digits": 1, "event": "lifetime_t850_k", "reducer": "mean"},
    "mean_t700_k": {"label": "Mean 700 hPa temperature", "group": "Temperature and core", "unit": "K", "digits": 1, "event": "lifetime_t700_k", "reducer": "mean"},
    "mean_t500_k": {"label": "Mean 500 hPa temperature", "group": "Temperature and core", "unit": "K", "digits": 1, "event": "lifetime_t500_k", "reducer": "mean"},
    "mean_t850_core_anomaly_k": {"label": "Mean 850 hPa core temperature anomaly", "group": "Temperature and core", "unit": "K", "digits": 2, "event": "lifetime_t850_core_anomaly_k", "reducer": "mean"},
    "mean_t700_core_anomaly_k": {"label": "Mean 700 hPa core temperature anomaly", "group": "Temperature and core", "unit": "K", "digits": 2, "event": "lifetime_t700_core_anomaly_k", "reducer": "mean"},
    "mean_t500_core_anomaly_k": {"label": "Mean 500 hPa core temperature anomaly", "group": "Temperature and core", "unit": "K", "digits": 2, "event": "lifetime_t500_core_anomaly_k", "reducer": "mean"},
    "mean_deep_core_temperature_anomaly_k": {"label": "Mean deep-layer core temperature anomaly", "group": "Temperature and core", "unit": "K", "digits": 2, "event": "lifetime_deep_core_temperature_anomaly_k", "reducer": "mean"},
    "mean_t850_minus_t500_k": {"label": "Mean T850 − T500", "group": "Temperature and core", "unit": "K", "digits": 1, "event": "lifetime_t850_minus_t500_k", "reducer": "mean"},
    "mean_t700_minus_t500_k": {"label": "Mean T700 − T500", "group": "Temperature and core", "unit": "K", "digits": 1, "event": "lifetime_t700_minus_t500_k", "reducer": "mean"},
    "mean_background_wind_ms": {"label": "Mean background wind speed", "group": "Surface and environment", "unit": "m s⁻¹", "digits": 1, "event": "lifetime_background_wind_ms", "reducer": "mean"},
    "mean_background_u_ms": {"label": "Mean zonal background wind", "group": "Surface and environment", "unit": "m s⁻¹", "digits": 1, "event": "lifetime_background_u_ms", "reducer": "mean"},
    "mean_background_v_ms": {"label": "Mean meridional background wind", "group": "Surface and environment", "unit": "m s⁻¹", "digits": 1, "event": "lifetime_background_v_ms", "reducer": "mean"},
    "mean_land_fraction": {"label": "Mean lifetime land fraction", "group": "Surface and environment", "unit": "fraction", "digits": 2, "event": "lifetime_land_fraction", "reducer": "mean"},
    "mean_orography_m": {"label": "Mean centre orography", "group": "Surface and environment", "unit": "m", "digits": 0, "event": "lifetime_orography_m", "reducer": "mean"},
}
CHANGE_METRICS = tuple(METRIC_DEFINITIONS)
ABSOLUTE_CHANGE_METRICS = {
    "depressions_or_stronger",
    "deep_depressions_or_stronger",
    "cyclonic_storms_or_stronger",
    "mean_westward_displacement_deg",
    "mean_northward_displacement_deg",
    "mean_genesis_lon",
    "mean_genesis_lat",
    "mean_lysis_lon",
    "mean_lysis_lat",
    "mean_minimum_mslp_hpa",
    "mean_environmental_mslp_hpa",
    "mean_peak_closed_isobars",
    "mean_vorticity_500_to_850_ratio",
    "mean_t850_k",
    "mean_t700_k",
    "mean_t500_k",
    "mean_t850_core_anomaly_k",
    "mean_t700_core_anomaly_k",
    "mean_t500_core_anomaly_k",
    "mean_deep_core_temperature_anomaly_k",
    "mean_t850_minus_t500_k",
    "mean_t700_minus_t500_k",
    "mean_background_u_ms",
    "mean_background_v_ms",
    "mean_land_fraction",
    "mean_orography_m",
}
ANNUAL_COLUMNS = ("year", *CHANGE_METRICS)
EVENT_COLUMNS = (
    "track_id",
    "start",
    "end",
    "model_start",
    "model_end",
    "model_calendar",
    "genesis_year",
    "genesis_month",
    "duration_hours",
    "path_length_km",
    "translation_speed_kmh",
    "westward_displacement_deg",
    "northward_displacement_deg",
    "genesis_lon",
    "genesis_lat",
    "lysis_lon",
    "lysis_lat",
    "peak_category",
    "peak_wind_ms",
    "peak_pressure_deficit_hpa",
    "peak_vorticity_x1e5_s1",
    "peak_24h_precipitation_mm",
    "mean_24h_precipitation_mm",
    "peak_1h_precipitation_mm",
    "peak_max_wind_ms",
    "lifetime_mean_wind_ms",
    "minimum_mslp_hpa",
    "lifetime_environmental_mslp_hpa",
    "peak_closed_isobars",
    "lifetime_vorticity_850_x1e5_s1",
    "lifetime_vorticity_700_x1e5_s1",
    "lifetime_vorticity_500_x1e5_s1",
    "lifetime_vorticity_deep_x1e5_s1",
    "lifetime_vorticity_500_to_850_ratio",
    "lifetime_q850_gkg",
    "lifetime_q700_gkg",
    "lifetime_q500_gkg",
    "lifetime_q_deep_gkg",
    "lifetime_rh850_pct",
    "lifetime_rh700_pct",
    "lifetime_rh500_pct",
    "lifetime_rh_deep_pct",
    "lifetime_t850_k",
    "lifetime_t700_k",
    "lifetime_t500_k",
    "lifetime_t850_core_anomaly_k",
    "lifetime_t700_core_anomaly_k",
    "lifetime_t500_core_anomaly_k",
    "lifetime_deep_core_temperature_anomaly_k",
    "lifetime_t850_minus_t500_k",
    "lifetime_t700_minus_t500_k",
    "lifetime_background_wind_ms",
    "lifetime_background_u_ms",
    "lifetime_background_v_ms",
    "lifetime_land_fraction",
    "lifetime_orography_m",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_gzip_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_bytes(compressed)
    os.replace(temporary, path)


def haversine_steps(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    if len(lon) < 2:
        return np.asarray([], dtype=float)
    lon_r = np.deg2rad(lon)
    lat_r = np.deg2rad(lat)
    dlon = np.diff(lon_r)
    dlat = np.diff(lat_r)
    value = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat_r[:-1]) * np.cos(lat_r[1:]) * np.sin(dlon / 2.0) ** 2
    )
    return 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))


def _numeric_values(frame: pd.DataFrame, column: str) -> np.ndarray:
    """Return finite numeric values, or an empty array for an absent diagnostic."""

    if column not in frame:
        return np.asarray([], dtype=float)
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    return values[np.isfinite(values)]


def _track_stat(frame: pd.DataFrame, column: str, reducer: str = "mean") -> float:
    values = _numeric_values(frame, column)
    if not len(values):
        return float("nan")
    if reducer == "mean":
        return float(values.mean())
    if reducer == "max":
        return float(values.max())
    if reducer == "min":
        return float(values.min())
    raise ValueError(f"unsupported track reducer {reducer}")


def _event_metric(group: pd.DataFrame, column: str, reducer: str) -> float:
    values = _numeric_values(group, column)
    if not len(values):
        return float("nan")
    if reducer == "mean":
        return float(values.mean())
    if reducer == "median":
        return float(np.median(values))
    if reducer == "p90":
        return float(np.quantile(values, 0.9))
    raise ValueError(f"unsupported event reducer {reducer}")


def event_summary(frame: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for track_id, group in frame.groupby("track_id", sort=True):
        group = group.sort_values("time")
        lon = pd.to_numeric(group.lon, errors="coerce").to_numpy(float)
        lat = pd.to_numeric(group.lat, errors="coerce").to_numpy(float)
        has_native_time = {"model_time", "model_year", "model_month"}.issubset(group.columns)
        if has_native_time:
            genesis_year = int(group.model_year.iloc[0])
            genesis_month = int(group.model_month.iloc[0])
            model_start = str(group.model_time.iloc[0])
            model_end = str(group.model_time.iloc[-1])
            model_calendar = str(group.model_calendar.iloc[0]) if "model_calendar" in group else None
        else:
            genesis_year = int(group.time.iloc[0].year)
            genesis_month = int(group.time.iloc[0].month)
            model_start = group.time.iloc[0].isoformat()
            model_end = group.time.iloc[-1].isoformat()
            model_calendar = "proleptic_gregorian"
        path_length_km = float(np.nansum(haversine_steps(lon, lat)))
        elapsed_hours = max(1.0, float((group.time.iloc[-1] - group.time.iloc[0]).total_seconds() / 3600.0))
        background_u = pd.to_numeric(
            group.get("background_u_300_500km_ms", pd.Series(np.nan, index=group.index)),
            errors="coerce",
        ).to_numpy(dtype=float)
        background_v = pd.to_numeric(
            group.get("background_v_300_500km_ms", pd.Series(np.nan, index=group.index)),
            errors="coerce",
        ).to_numpy(dtype=float)
        background_speed = np.hypot(background_u, background_v)
        records.append(
            {
                "track_id": int(track_id),
                "start": group.time.iloc[0],
                "end": group.time.iloc[-1],
                "model_start": model_start,
                "model_end": model_end,
                "model_calendar": model_calendar,
                "genesis_year": genesis_year,
                "genesis_month": genesis_month,
                "duration_hours": int(len(group)),
                "path_length_km": path_length_km,
                "translation_speed_kmh": path_length_km / elapsed_hours,
                "westward_displacement_deg": float(lon[0] - lon[-1]),
                "northward_displacement_deg": float(lat[-1] - lat[0]),
                "genesis_lon": float(lon[0]),
                "genesis_lat": float(lat[0]),
                "lysis_lon": float(lon[-1]),
                "lysis_lat": float(lat[-1]),
                "peak_category": int(_track_stat(group, "event_peak_imd_category", "max")),
                "peak_wind_ms": _track_stat(group, "p95_anomaly_wind_125km_ms", "max"),
                "peak_pressure_deficit_hpa": _track_stat(group, "pressure_deficit_hpa", "max"),
                "peak_vorticity_x1e5_s1": _track_stat(group, "max_vort_smoothed", "max"),
                "peak_24h_precipitation_mm": _track_stat(group, "precip_24hr", "max"),
                "mean_24h_precipitation_mm": _track_stat(group, "precip_24hr"),
                "peak_1h_precipitation_mm": _track_stat(group, "precip_1hr", "max"),
                "peak_max_wind_ms": _track_stat(group, "max_wind", "max"),
                "lifetime_mean_wind_ms": _track_stat(group, "mean_wind"),
                "minimum_mslp_hpa": _track_stat(group, "min_mslp", "min"),
                "lifetime_environmental_mslp_hpa": _track_stat(group, "ring_mslp_p60"),
                "peak_closed_isobars": _track_stat(group, "closed_isobars_2hpa_actual", "max"),
                "lifetime_vorticity_850_x1e5_s1": _track_stat(group, "mean_vort_850"),
                "lifetime_vorticity_700_x1e5_s1": _track_stat(group, "mean_vort_700"),
                "lifetime_vorticity_500_x1e5_s1": _track_stat(group, "mean_vort_500"),
                "lifetime_vorticity_deep_x1e5_s1": _track_stat(group, "mean_vort_deep"),
                "lifetime_vorticity_500_to_850_ratio": _track_stat(group, "vort500_to_850_ratio"),
                "lifetime_q850_gkg": _track_stat(group, "q850_mean_gkg"),
                "lifetime_q700_gkg": _track_stat(group, "q700_mean_gkg"),
                "lifetime_q500_gkg": _track_stat(group, "q500_mean_gkg"),
                "lifetime_q_deep_gkg": _track_stat(group, "q_deep_mean_gkg"),
                "lifetime_rh850_pct": _track_stat(group, "rh850_mean_pct"),
                "lifetime_rh700_pct": _track_stat(group, "rh700_mean_pct"),
                "lifetime_rh500_pct": _track_stat(group, "rh500_mean_pct"),
                "lifetime_rh_deep_pct": _track_stat(group, "rh_deep_mean_pct"),
                "lifetime_t850_k": _track_stat(group, "t850_mean_k"),
                "lifetime_t700_k": _track_stat(group, "t700_mean_k"),
                "lifetime_t500_k": _track_stat(group, "t500_mean_k"),
                "lifetime_t850_core_anomaly_k": _track_stat(group, "t850_inner_minus_annulus_k"),
                "lifetime_t700_core_anomaly_k": _track_stat(group, "t700_inner_minus_annulus_k"),
                "lifetime_t500_core_anomaly_k": _track_stat(group, "t500_inner_minus_annulus_k"),
                "lifetime_deep_core_temperature_anomaly_k": _track_stat(group, "t_inner_minus_annulus_deep_k"),
                "lifetime_t850_minus_t500_k": _track_stat(group, "t850_minus_t500_k"),
                "lifetime_t700_minus_t500_k": _track_stat(group, "t700_minus_t500_k"),
                "lifetime_background_wind_ms": float(np.nanmean(background_speed)) if np.isfinite(background_speed).any() else float("nan"),
                "lifetime_background_u_ms": _track_stat(group, "background_u_300_500km_ms"),
                "lifetime_background_v_ms": _track_stat(group, "background_v_300_500km_ms"),
                "lifetime_land_fraction": _track_stat(group, "land_fraction"),
                "lifetime_orography_m": _track_stat(group, "orography_m"),
            }
        )
    return pd.DataFrame.from_records(records, columns=EVENT_COLUMNS)


def annual_summary(
    events: pd.DataFrame,
    start_year: int,
    end_year: int,
    months: tuple[int, ...] = SEASONS["all"],
) -> pd.DataFrame:
    events = events.loc[events.genesis_month.isin(months)]
    years = pd.DataFrame({"year": np.arange(start_year, end_year + 1, dtype=int)})
    rows: list[dict[str, Any]] = []
    for year, group in events.groupby("genesis_year"):
        row: dict[str, Any] = {
            "year": int(year),
            "systems": int(len(group)),
            "depressions_or_stronger": int(group.peak_category.ge(2).sum()),
            "deep_depressions_or_stronger": int(group.peak_category.ge(3).sum()),
            "cyclonic_storms_or_stronger": int(group.peak_category.ge(4).sum()),
            "system_days": float(group.duration_hours.sum() / 24.0),
        }
        for metric, definition in METRIC_DEFINITIONS.items():
            event_column = definition.get("event")
            if event_column:
                row[metric] = _event_metric(group, str(event_column), str(definition["reducer"]))
        rows.append(row)
    annual = years.merge(pd.DataFrame.from_records(rows, columns=ANNUAL_COLUMNS), on="year", how="left")
    count_columns = ["systems", "depressions_or_stronger", "deep_depressions_or_stronger", "cyclonic_storms_or_stronger", "system_days"]
    for column in count_columns:
        annual[column] = pd.to_numeric(annual[column], errors="coerce").fillna(0)
    return annual


def monthly_summary(events: pd.DataFrame, years: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for month in range(1, 13):
        group = events.loc[events.genesis_month.eq(month)]
        records.append(
            {
                "month": month,
                "systems_per_year": len(group) / years,
                "depressions_or_stronger_per_year": int(group.peak_category.ge(2).sum()) / years,
                "mean_duration_hours": float(group.duration_hours.mean()) if len(group) else None,
                "mean_peak_wind_ms": float(group.peak_wind_ms.mean()) if len(group) else None,
                "mean_peak_precipitation_mm": float(group.peak_24h_precipitation_mm.mean()) if len(group) else None,
            }
        )
    return records


def density(frame: pd.DataFrame) -> dict[str, Any]:
    counts = np.zeros((len(LAT_EDGES) - 1, len(LON_EDGES) - 1), dtype=np.int32)
    for _track_id, group in frame.groupby("track_id", sort=False):
        lon = pd.to_numeric(group.lon, errors="coerce").to_numpy(float)
        lat = pd.to_numeric(group.lat, errors="coerce").to_numpy(float)
        i = np.searchsorted(LAT_EDGES, lat, side="right") - 1
        j = np.searchsorted(LON_EDGES, lon, side="right") - 1
        valid = (i >= 0) & (i < counts.shape[0]) & (j >= 0) & (j < counts.shape[1])
        cells = np.unique(np.column_stack([i[valid], j[valid]]), axis=0)
        if len(cells):
            counts[cells[:, 0], cells[:, 1]] += 1
    return {
        "latitude_edges": LAT_EDGES.tolist(),
        "longitude_edges": LON_EDGES.tolist(),
        "unique_track_counts": counts.tolist(),
    }


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    safe = frame.copy()
    for column in safe.select_dtypes(include=["datetime", "datetimetz"]).columns:
        safe[column] = safe[column].dt.strftime("%Y-%m-%dT%H:%M:%S")
    return safe.replace({np.nan: None}).to_dict("records")


def public_metric_definitions() -> dict[str, dict[str, Any]]:
    """Return browser metadata without the private event-reduction recipe."""

    return {
        metric: {
            key: value
            for key, value in definition.items()
            if key not in {"event", "reducer"}
        }
        | {"change_mode": "absolute" if metric in ABSOLUTE_CHANGE_METRICS else "percent"}
        for metric, definition in METRIC_DEFINITIONS.items()
    }


def available_metrics(annual: pd.DataFrame) -> list[str]:
    return [
        metric
        for metric in CHANGE_METRICS
        if metric in annual and pd.to_numeric(annual[metric], errors="coerce").notna().any()
    ]


def seasonal_summary(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Summarise complete tracks selected by their genesis season."""

    result: dict[str, Any] = {}
    for key, months in SEASONS.items():
        selected_events = events.loc[events.genesis_month.isin(months)]
        selected_ids = set(selected_events.track_id.astype(int))
        selected_positions = frame.loc[frame.track_id.isin(selected_ids)]
        genesis_points = selected_events[["track_id", "genesis_lon", "genesis_lat"]].rename(
            columns={"genesis_lon": "lon", "genesis_lat": "lat"}
        )
        lysis_points = selected_events[["track_id", "lysis_lon", "lysis_lat"]].rename(
            columns={"lysis_lon": "lon", "lysis_lat": "lat"}
        )
        result[key] = {
            "months": list(months),
            "counts": {
                "events": int(len(selected_events)),
                "positions": int(len(selected_positions)),
            },
            "annual": _json_records(annual_summary(events, start_year, end_year, months)),
            "class_counts": {
                str(category): int(count)
                for category, count in selected_events.peak_category.value_counts().sort_index().items()
            },
            "track_density": density(selected_positions),
            "genesis_density": density(genesis_points),
            "lysis_density": density(lysis_points),
        }
    return result


def summarise_run(
    catalogue: Path,
    output_dir: Path,
    *,
    source_label: str,
    experiment_id: str,
    member_id: str,
    period_label: str,
    start_year: int | None = None,
    end_year: int | None = None,
    qa_report: Path | None = None,
) -> Path:
    frame = pd.read_parquet(catalogue)
    frame["time"] = pd.to_datetime(frame.time, errors="raise")
    if frame.empty:
        raise ValueError("cannot summarise an empty CMIP6 catalogue")
    events = event_summary(frame)
    observed_start_year = int(events.genesis_year.min())
    observed_end_year = int(events.genesis_year.max())
    start_year = observed_start_year if start_year is None else int(start_year)
    end_year = observed_end_year if end_year is None else int(end_year)
    if start_year > observed_start_year or end_year < observed_end_year or start_year > end_year:
        raise ValueError(
            f"summary coverage {start_year}--{end_year} does not contain catalogue years "
            f"{observed_start_year}--{observed_end_year}"
        )
    years = end_year - start_year + 1
    annual = annual_summary(events, start_year, end_year)
    output_dir.mkdir(parents=True, exist_ok=True)
    event_path = output_dir / "events.parquet"
    annual_path = output_dir / "annual.parquet"
    atomic_parquet(event_path, events)
    atomic_parquet(annual_path, annual)
    qa: dict[str, Any] | None = None
    if qa_report is not None:
        qa_payload = json.loads(qa_report.read_text(encoding="utf-8"))
        if qa_payload.get("schema") != "lps-atlas-cmip6-catalogue-qa-v1":
            raise ValueError(f"unsupported CMIP6 QA schema in {qa_report}")
        if qa_payload.get("status") != "passed":
            raise ValueError(f"CMIP6 catalogue QA did not pass: {qa_report}")
        if qa_payload.get("catalogue", {}).get("sha256") != sha256(catalogue):
            raise ValueError(f"CMIP6 QA report does not describe {catalogue}")
        qa = {
            "schema": qa_payload["schema"],
            "status": qa_payload["status"],
            "report_sha256": sha256(qa_report),
            "checks": qa_payload.get("checks", {}),
            "historical_screen": qa_payload.get("historical_screen"),
        }
    payload = {
        "schema": SCHEMA,
        "run": {
            "source_label": source_label,
            "experiment_id": experiment_id,
            "member_id": member_id,
            "period_label": period_label,
        },
        "coverage": {"start_year": start_year, "end_year": end_year, "years": years},
        "counts": {"events": len(events), "positions": len(frame)},
        "metric_definitions": public_metric_definitions(),
        "capabilities": {
            "available_metrics": available_metrics(annual),
            "unavailable_metrics": sorted(set(CHANGE_METRICS) - set(available_metrics(annual))),
            "availability_rule": "at least one finite annual value in this run",
        },
        "season_definitions": {key: list(months) for key, months in SEASONS.items()},
        "seasonal": seasonal_summary(frame, events, start_year, end_year),
        "monthly": monthly_summary(events, years),
        "provenance": {
            "catalogue_filename": catalogue.name,
            "catalogue_sha256": sha256(catalogue),
            "intensity_method": sorted(frame.intensity_method.astype(str).unique().tolist()),
            "model_calendars": (
                sorted(frame.model_calendar.astype(str).unique().tolist())
                if "model_calendar" in frame else ["proleptic_gregorian"]
            ),
            "time_basis": (
                sorted(frame.time_basis.astype(str).unique().tolist())
                if "time_basis" in frame else ["civil_gregorian"]
            ),
        },
        "qa": qa,
    }
    unhashed = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(unhashed).hexdigest()
    asset = output_dir / f"climate-run.{digest[:12]}.json.gz"
    atomic_gzip_json(asset, payload)
    atomic_json(
        output_dir / "manifest.json",
        {
            "schema": SCHEMA,
            "generated_utc": utc_now(),
            "run": payload["run"],
            "coverage": payload["coverage"],
            "asset": {"path": asset.name, "sha256": sha256(asset), "bytes": asset.stat().st_size},
            "events": {"path": event_path.name, "sha256": sha256(event_path), "rows": len(events)},
            "annual": {"path": annual_path.name, "sha256": sha256(annual_path), "rows": len(annual)},
        },
    )
    return asset


def bootstrap_change(
    historical: np.ndarray,
    future: np.ndarray,
    *,
    seed: int,
    samples: int = 5000,
) -> dict[str, float | None]:
    left = np.asarray(historical, dtype=float)
    right = np.asarray(future, dtype=float)
    left = left[np.isfinite(left)]
    right = right[np.isfinite(right)]
    if not len(left) or not len(right):
        return {
            "historical": None,
            "future": None,
            "absolute_change": None,
            "percent_change": None,
            "ci05": None,
            "ci95": None,
            "percent_ci05": None,
            "percent_ci95": None,
        }
    rng = np.random.default_rng(seed)
    future_draws = right[rng.integers(0, len(right), size=(samples, len(right)))].mean(axis=1)
    historical_draws = left[rng.integers(0, len(left), size=(samples, len(left)))].mean(axis=1)
    differences = future_draws - historical_draws
    percentage_draws = np.divide(
        differences * 100.0,
        historical_draws,
        out=np.full(differences.shape, np.nan),
        where=historical_draws != 0,
    )
    percentage_draws = percentage_draws[np.isfinite(percentage_draws)]
    historical_mean = float(left.mean())
    future_mean = float(right.mean())
    return {
        "historical": historical_mean,
        "future": future_mean,
        "absolute_change": future_mean - historical_mean,
        "percent_change": ((future_mean / historical_mean) - 1.0) * 100.0 if historical_mean != 0 else None,
        "ci05": float(np.quantile(differences, 0.05)),
        "ci95": float(np.quantile(differences, 0.95)),
        "percent_ci05": float(np.quantile(percentage_draws, 0.05)) if len(percentage_draws) else None,
        "percent_ci95": float(np.quantile(percentage_draws, 0.95)) if len(percentage_draws) else None,
    }


def _finite_mean(values: list[Any]) -> float | None:
    clean = np.asarray(
        [float(value) for value in values if value is not None and np.isfinite(float(value))],
        dtype=float,
    )
    return float(clean.mean()) if len(clean) else None


def _aggregate_density(
    payloads: list[dict[str, Any]],
    season: str,
    years: int,
    density_key: str = "track_density",
) -> dict[str, Any]:
    densities = [payload["seasonal"][season][density_key] for payload in payloads]
    latitude_edges = densities[0]["latitude_edges"]
    longitude_edges = densities[0]["longitude_edges"]
    rates: list[np.ndarray] = []
    for payload, density_payload in zip(payloads, densities, strict=True):
        if (
            density_payload["latitude_edges"] != latitude_edges
            or density_payload["longitude_edges"] != longitude_edges
        ):
            raise ValueError("CMIP6 density grids do not match")
        model_years = int(payload["coverage"]["years"])
        rates.append(np.asarray(density_payload["unique_track_counts"], dtype=float) / model_years)
    # Preserve the run-summary contract: the browser divides these counts by
    # coverage years, so store the one-model-one-vote annual rate times years.
    counts = np.mean(np.stack(rates), axis=0) * years
    return {
        "latitude_edges": latitude_edges,
        "longitude_edges": longitude_edges,
        "unique_track_counts": counts.tolist(),
        "aggregation": "one_model_one_vote_annual_rate",
    }


def _density_agreement(
    historical_payloads: list[dict[str, Any]],
    future_payloads: list[dict[str, Any]],
    season: str,
) -> dict[str, Any]:
    historical_densities = [
        payload["seasonal"][season]["track_density"] for payload in historical_payloads
    ]
    future_densities = [
        payload["seasonal"][season]["track_density"] for payload in future_payloads
    ]
    latitude_edges = historical_densities[0]["latitude_edges"]
    longitude_edges = historical_densities[0]["longitude_edges"]
    differences: list[np.ndarray] = []
    for historical, future, historical_density, future_density in zip(
        historical_payloads,
        future_payloads,
        historical_densities,
        future_densities,
        strict=True,
    ):
        if any(
            density["latitude_edges"] != latitude_edges
            or density["longitude_edges"] != longitude_edges
            for density in (historical_density, future_density)
        ):
            raise ValueError("CMIP6 density grids do not match for sign agreement")
        historical_rate = np.asarray(
            historical_density["unique_track_counts"], dtype=float
        ) / int(historical["coverage"]["years"])
        future_rate = np.asarray(
            future_density["unique_track_counts"], dtype=float
        ) / int(future["coverage"]["years"])
        differences.append(future_rate - historical_rate)
    stack = np.stack(differences)
    positive = (stack > 1e-12).sum(axis=0)
    negative = (stack < -1e-12).sum(axis=0)
    unchanged = len(stack) - positive - negative
    dominant = np.maximum(positive, negative)
    sign = np.where(positive > negative, 1, np.where(negative > positive, -1, 0))
    agreement = dominant / len(stack)
    signed_agreement = sign * agreement
    any_change = dominant > 0
    robust_threshold = int(np.ceil(0.8 * len(stack)))
    return {
        "latitude_edges": latitude_edges,
        "longitude_edges": longitude_edges,
        "model_count": len(stack),
        "positive_models": positive.astype(int).tolist(),
        "negative_models": negative.astype(int).tolist(),
        "unchanged_models": unchanged.astype(int).tolist(),
        "signed_agreement_fraction": np.round(signed_agreement, 4).tolist(),
        "mean_change_per_year": np.round(np.mean(stack, axis=0), 6).tolist(),
        "summary": {
            "cells_with_any_change": int(any_change.sum()),
            "cells_at_least_80_percent_agreement": int(
                (any_change & (dominant >= robust_threshold)).sum()
            ),
            "cells_unanimous": int((any_change & (dominant == len(stack))).sum()),
            "robust_model_threshold": robust_threshold,
        },
        "definition": (
            "sign gives the dominant future-minus-historical direction; magnitude is the "
            "fraction of equally weighted models with that sign, with unchanged models retained"
        ),
    }


def aggregate_run_payloads(
    payloads: list[dict[str, Any]],
    *,
    role: str,
    model_ids: list[str],
) -> dict[str, Any]:
    """Create a run-shaped one-model-one-vote payload for atlas rendering."""
    if len(payloads) != len(model_ids) or len(payloads) < 2:
        raise ValueError("multi-model summaries require at least two matched model payloads")
    if any(payload.get("schema") != SCHEMA for payload in payloads):
        raise ValueError("multi-model inputs must use the current run-summary schema")
    years_set = {int(payload["coverage"]["years"]) for payload in payloads}
    if len(years_set) != 1:
        raise ValueError("multi-model windows must have equal lengths")
    years = years_set.pop()
    experiments = sorted({str(payload["run"]["experiment_id"]) for payload in payloads})
    if len(experiments) != 1:
        raise ValueError(f"{role} payloads mix experiments: {experiments}")
    seasons = list(SEASONS)
    seasonal: dict[str, Any] = {}
    for season in seasons:
        annual_inputs = [payload["seasonal"][season]["annual"] for payload in payloads]
        if any(len(rows) != years for rows in annual_inputs):
            raise ValueError(f"{season} annual windows do not match their declared length")
        annual: list[dict[str, Any]] = []
        for ordinal in range(years):
            row: dict[str, Any] = {"year": ordinal + 1}
            for metric in CHANGE_METRICS:
                row[metric] = _finite_mean([rows[ordinal].get(metric) for rows in annual_inputs])
            annual.append(row)

        category_keys = sorted(
            {
                str(category)
                for payload in payloads
                for category in payload["seasonal"][season]["class_counts"]
            },
            key=int,
        )
        model_shares: list[dict[str, float]] = []
        for payload in payloads:
            counts = payload["seasonal"][season]["class_counts"]
            total = sum(float(value) for value in counts.values())
            if total > 0:
                model_shares.append(
                    {category: float(counts.get(category, 0)) / total for category in category_keys}
                )
        class_counts = {
            category: _finite_mean([share[category] for share in model_shares]) or 0.0
            for category in category_keys
        }
        seasonal[season] = {
            "months": list(SEASONS[season]),
            "counts": {
                "events": _finite_mean(
                    [payload["seasonal"][season]["counts"]["events"] for payload in payloads]
                ),
                "positions": _finite_mean(
                    [payload["seasonal"][season]["counts"]["positions"] for payload in payloads]
                ),
            },
            "annual": annual,
            "class_counts": class_counts,
            "track_density": _aggregate_density(payloads, season, years, "track_density"),
            "genesis_density": _aggregate_density(payloads, season, years, "genesis_density"),
            "lysis_density": _aggregate_density(payloads, season, years, "lysis_density"),
        }

    monthly: list[dict[str, Any]] = []
    monthly_metrics = (
        "systems_per_year",
        "depressions_or_stronger_per_year",
        "mean_duration_hours",
        "mean_peak_wind_ms",
        "mean_peak_precipitation_mm",
    )
    for month_index in range(12):
        row = {"month": month_index + 1}
        for metric in monthly_metrics:
            row[metric] = _finite_mean(
                [payload["monthly"][month_index].get(metric) for payload in payloads]
            )
        monthly.append(row)

    windows = [
        {
            "model_id": model_id,
            "source_label": payload["run"]["source_label"],
            "period_label": payload["run"]["period_label"],
            "start_year": int(payload["coverage"]["start_year"]),
            "end_year": int(payload["coverage"]["end_year"]),
        }
        for model_id, payload in zip(model_ids, payloads, strict=True)
    ]
    period_labels = {window["period_label"] for window in windows}
    return {
        "schema": SCHEMA,
        "run": {
            "kind": "multi-model",
            "source_label": "Multi-model mean",
            "experiment_id": experiments[0],
            "member_id": "one-model-one-vote",
            "period_label": (
                next(iter(period_labels))
                if len(period_labels) == 1
                else ("Historical model windows" if role == "historical" else "Future model windows")
            ),
        },
        "coverage": {
            "start_year": min(window["start_year"] for window in windows),
            "end_year": max(window["end_year"] for window in windows),
            "years": years,
            "model_windows": windows,
        },
        "counts": {
            "events": _finite_mean([payload["counts"]["events"] for payload in payloads]),
            "positions": _finite_mean([payload["counts"]["positions"] for payload in payloads]),
        },
        "metric_definitions": public_metric_definitions(),
        "capabilities": {
            "available_metrics": [
                metric
                for metric in CHANGE_METRICS
                if any(
                    metric in set(payload.get("capabilities", {}).get("available_metrics", CHANGE_METRICS))
                    for payload in payloads
                )
            ],
            "metric_model_counts": {
                metric: sum(
                    metric in set(payload.get("capabilities", {}).get("available_metrics", CHANGE_METRICS))
                    for payload in payloads
                )
                for metric in CHANGE_METRICS
            },
            "availability_rule": "available in at least one equally weighted source model",
        },
        "season_definitions": {key: list(months) for key, months in SEASONS.items()},
        "seasonal": seasonal,
        "monthly": monthly,
        "provenance": {
            "aggregation": "one_model_one_vote",
            "model_ids": model_ids,
            "model_count": len(model_ids),
            "source_catalogue_sha256": [
                payload.get("provenance", {}).get("catalogue_sha256") for payload in payloads
            ],
        },
        "qa": {
            "status": "multi-model-candidate",
            "model_count": len(model_ids),
            "models": [
                {
                    "id": model_id,
                    "historical_screening_status": (
                        payload.get("qa", {}).get("historical_screen", {}) or {}
                    ).get("screening_status"),
                }
                for model_id, payload in zip(model_ids, payloads, strict=True)
            ],
        },
    }


def aggregate_change_payloads(
    historical_payloads: list[dict[str, Any]],
    future_payloads: list[dict[str, Any]],
    *,
    model_ids: list[str],
    samples: int = 5000,
) -> dict[str, Any]:
    """Aggregate paired changes with equal weight for every source model."""
    if not (len(historical_payloads) == len(future_payloads) == len(model_ids)):
        raise ValueError("historical, future and model lists do not align")
    seasonal_changes: dict[str, Any] = {}
    density_agreement: dict[str, Any] = {}
    for season_index, season in enumerate(SEASONS):
        density_agreement[season] = _density_agreement(
            historical_payloads, future_payloads, season
        )
        metrics: dict[str, Any] = {}
        for metric_index, metric in enumerate(CHANGE_METRICS):
            model_values: list[dict[str, Any]] = []
            bootstrap_historical: list[np.ndarray] = []
            bootstrap_future: list[np.ndarray] = []
            bootstrap_differences: list[np.ndarray] = []
            bootstrap_model_percentages: list[np.ndarray] = []
            rng = np.random.default_rng(1913 + season_index * 100 + metric_index)
            for model_id, historical, future in zip(
                model_ids, historical_payloads, future_payloads, strict=True
            ):
                left = np.asarray(
                    [row.get(metric) for row in historical["seasonal"][season]["annual"]],
                    dtype=float,
                )
                right = np.asarray(
                    [row.get(metric) for row in future["seasonal"][season]["annual"]],
                    dtype=float,
                )
                left = left[np.isfinite(left)]
                right = right[np.isfinite(right)]
                if not len(left) or not len(right):
                    continue
                historical_mean = float(left.mean())
                future_mean = float(right.mean())
                absolute = future_mean - historical_mean
                percent = (
                    (future_mean / historical_mean - 1.0) * 100.0
                    if historical_mean != 0 else None
                )
                left_draw = left[rng.integers(0, len(left), size=(samples, len(left)))].mean(axis=1)
                right_draw = right[rng.integers(0, len(right), size=(samples, len(right)))].mean(axis=1)
                difference_draw = right_draw - left_draw
                percent_draw = np.full(samples, np.nan, dtype=float)
                nonzero_model = left_draw != 0
                percent_draw[nonzero_model] = (
                    right_draw[nonzero_model] / left_draw[nonzero_model] - 1.0
                ) * 100.0
                finite_percent_draw = percent_draw[np.isfinite(percent_draw)]
                model_values.append(
                    {
                        "id": model_id,
                        "historical": historical_mean,
                        "future": future_mean,
                        "absolute_change": absolute,
                        "percent_change": percent,
                        "ci05": float(np.quantile(difference_draw, 0.05)),
                        "ci95": float(np.quantile(difference_draw, 0.95)),
                        "percent_ci05": (
                            float(np.quantile(finite_percent_draw, 0.05))
                            if len(finite_percent_draw) else None
                        ),
                        "percent_ci95": (
                            float(np.quantile(finite_percent_draw, 0.95))
                            if len(finite_percent_draw) else None
                        ),
                    }
                )
                bootstrap_historical.append(left_draw)
                bootstrap_future.append(right_draw)
                bootstrap_differences.append(difference_draw)
                if historical_mean != 0:
                    bootstrap_model_percentages.append(percent_draw)
            if not model_values:
                metrics[metric] = {
                    "historical": None,
                    "future": None,
                    "absolute_change": None,
                    "percent_change": None,
                    "ci05": None,
                    "ci95": None,
                    "percent_ci05": None,
                    "percent_ci95": None,
                    "mean_model_percent_change": None,
                    "mean_model_percent_ci05": None,
                    "mean_model_percent_ci95": None,
                    "model_spread05": None,
                    "model_spread95": None,
                    "model_count": 0,
                    "models": [],
                }
                continue
            historical_mean = _finite_mean(
                [value["historical"] for value in model_values]
            )
            future_mean = _finite_mean([value["future"] for value in model_values])
            combined = np.mean(np.stack(bootstrap_differences), axis=0)
            historical_draws = np.mean(np.stack(bootstrap_historical), axis=0)
            future_draws = np.mean(np.stack(bootstrap_future), axis=0)
            percentage_draws = np.full(samples, np.nan, dtype=float)
            nonzero = historical_draws != 0
            percentage_draws[nonzero] = (
                future_draws[nonzero] / historical_draws[nonzero] - 1.0
            ) * 100.0
            percentage_draws = percentage_draws[np.isfinite(percentage_draws)]
            if bootstrap_model_percentages:
                model_percentage_stack = np.stack(bootstrap_model_percentages)
                model_percentage_count = np.isfinite(model_percentage_stack).sum(axis=0)
                model_percentage_draws = np.divide(
                    np.nansum(model_percentage_stack, axis=0),
                    model_percentage_count,
                    out=np.full(samples, np.nan, dtype=float),
                    where=model_percentage_count > 0,
                )
            else:
                model_percentage_draws = np.asarray([], dtype=float)
            model_percentage_draws = model_percentage_draws[
                np.isfinite(model_percentage_draws)
            ]
            absolute_values = np.asarray(
                [value["absolute_change"] for value in model_values], dtype=float
            )
            metrics[metric] = {
                "historical": historical_mean,
                "future": future_mean,
                "absolute_change": future_mean - historical_mean,
                "percent_change": (
                    (future_mean / historical_mean - 1.0) * 100.0
                    if historical_mean != 0 else None
                ),
                "ci05": float(np.quantile(combined, 0.05)),
                "ci95": float(np.quantile(combined, 0.95)),
                "percent_ci05": (
                    float(np.quantile(percentage_draws, 0.05)) if len(percentage_draws) else None
                ),
                "percent_ci95": (
                    float(np.quantile(percentage_draws, 0.95)) if len(percentage_draws) else None
                ),
                "mean_model_percent_change": _finite_mean(
                    [value["percent_change"] for value in model_values]
                ),
                "mean_model_percent_ci05": (
                    float(np.quantile(model_percentage_draws, 0.05))
                    if len(model_percentage_draws) else None
                ),
                "mean_model_percent_ci95": (
                    float(np.quantile(model_percentage_draws, 0.95))
                    if len(model_percentage_draws) else None
                ),
                "model_spread05": float(np.quantile(absolute_values, 0.05)),
                "model_spread95": float(np.quantile(absolute_values, 0.95)),
                "model_count": len(model_values),
                "models": model_values,
            }
        seasonal_changes[season] = metrics
    return {
        "schema": ENSEMBLE_SCHEMA,
        "generated_utc": utc_now(),
        "aggregation": "one_model_one_vote",
        "model_ids": model_ids,
        "model_count": len(model_ids),
        "season_definitions": {key: list(months) for key, months in SEASONS.items()},
        "changes": seasonal_changes["all"],
        "seasonal_changes": seasonal_changes,
        "track_density_agreement": density_agreement,
        "uncertainty": (
            "5th--95th percentile of 5,000 within-model year-resampling draws; "
            "models retain equal weight in every draw. Percent-change intervals compare "
            "the equally weighted future and historical means"
        ),
        "percent_change_definition": (
            "100 * (equal-weight future model mean / equal-weight historical model mean - 1)"
        ),
        "mean_model_percent_change_definition": (
            "arithmetic mean of each model's paired percentage change; retained as a "
            "secondary diagnostic"
        ),
        "model_spread": "5th--95th percentile across equally weighted model mean changes",
        "interpretation": "Multi-model paired change with one source model, one vote.",
    }


def summarise_pair(historical_manifest: Path, future_manifest: Path, output_dir: Path) -> Path:
    historical_meta = json.loads(historical_manifest.read_text(encoding="utf-8"))
    future_meta = json.loads(future_manifest.read_text(encoding="utf-8"))
    historical_path = Path(historical_meta["annual"]["path"])
    future_path = Path(future_meta["annual"]["path"])
    if not historical_path.is_absolute():
        historical_path = historical_manifest.resolve().parent / historical_path
    if not future_path.is_absolute():
        future_path = future_manifest.resolve().parent / future_path
    historical = pd.read_parquet(historical_path)
    future = pd.read_parquet(future_path)
    historical_events_path = Path(historical_meta["events"]["path"])
    future_events_path = Path(future_meta["events"]["path"])
    if not historical_events_path.is_absolute():
        historical_events_path = historical_manifest.resolve().parent / historical_events_path
    if not future_events_path.is_absolute():
        future_events_path = future_manifest.resolve().parent / future_events_path
    historical_events = pd.read_parquet(historical_events_path)
    future_events = pd.read_parquet(future_events_path)
    historical_start, historical_end = int(historical.year.min()), int(historical.year.max())
    future_start, future_end = int(future.year.min()), int(future.year.max())
    seasonal_changes: dict[str, Any] = {}
    for season_index, (season, months) in enumerate(SEASONS.items()):
        left = annual_summary(historical_events, historical_start, historical_end, months)
        right = annual_summary(future_events, future_start, future_end, months)
        seasonal_changes[season] = {
            metric: bootstrap_change(
                left[metric].to_numpy(),
                right[metric].to_numpy(),
                seed=731 + season_index * 100 + metric_index,
            )
            for metric_index, metric in enumerate(CHANGE_METRICS)
        }
    payload = {
        "schema": PAIR_SCHEMA,
        "generated_utc": utc_now(),
        "historical": {
            "run": historical_meta.get("run"),
            "coverage": historical_meta.get("coverage"),
            "manifest_sha256": sha256(historical_manifest),
        },
        "future": {
            "run": future_meta.get("run"),
            "coverage": future_meta.get("coverage"),
            "manifest_sha256": sha256(future_manifest),
        },
        "season_definitions": {key: list(months) for key, months in SEASONS.items()},
        "changes": seasonal_changes["all"],
        "seasonal_changes": seasonal_changes,
        "uncertainty": "5th--95th percentile of 5,000 independent year-resampling bootstrap differences",
        "interpretation": "Single-model paired change; not a multi-model projection.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    asset = output_dir / f"climate-pair.{digest[:12]}.json.gz"
    atomic_gzip_json(asset, payload)
    atomic_json(
        output_dir / "manifest.json",
        {
            "schema": payload["schema"],
            "asset": {"path": asset.name, "sha256": sha256(asset), "bytes": asset.stat().st_size},
            "historical_manifest": str(historical_manifest.resolve()),
            "future_manifest": str(future_manifest.resolve()),
            "changes": seasonal_changes["all"],
        },
    )
    return asset


def _load_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _manifest_asset(manifest_path: Path) -> tuple[dict[str, Any], Path]:
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = Path(metadata["asset"]["path"])
    if not asset.is_absolute():
        asset = manifest_path.resolve().parent / asset
    if sha256(asset) != metadata["asset"]["sha256"]:
        raise ValueError(f"asset checksum does not match {manifest_path}")
    return metadata, asset


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def publish_pair(
    historical_manifest: Path,
    future_manifest: Path,
    pair_manifest: Path,
    output_dir: Path,
) -> Path:
    """Publish one validated pair as a relocatable, browser-facing bundle."""

    historical_meta, historical_asset = _manifest_asset(historical_manifest)
    future_meta, future_asset = _manifest_asset(future_manifest)
    pair_meta, pair_asset = _manifest_asset(pair_manifest)
    historical_payload = _load_gzip_json(historical_asset)
    future_payload = _load_gzip_json(future_asset)
    pair_payload = _load_gzip_json(pair_asset)
    if historical_payload.get("schema") != SCHEMA or future_payload.get("schema") != SCHEMA:
        raise ValueError("run summaries must use the current CMIP6 climate summary schema")
    if pair_payload.get("schema") != PAIR_SCHEMA:
        raise ValueError("paired summary must use the current CMIP6 paired-change schema")
    historical_run = historical_payload["run"]
    future_run = future_payload["run"]
    identity = (
        historical_run["source_label"],
        historical_run["member_id"],
        historical_run["experiment_id"],
        future_run["experiment_id"],
    )
    pair_id = hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:16]
    assets_dir = output_dir / "assets"
    copied: dict[str, dict[str, Any]] = {}
    for role, path, meta in (
        ("historical", historical_asset, historical_meta),
        ("future", future_asset, future_meta),
        ("change", pair_asset, pair_meta),
    ):
        destination = assets_dir / path.name
        _copy_atomic(path, destination)
        copied[role] = {
            "url": f"assets/{destination.name}",
            "sha256": meta["asset"]["sha256"],
            "bytes": destination.stat().st_size,
        }
    pair_record = {
        "id": pair_id,
        "comparison_basis": "time-slice",
        "source_label": historical_run["source_label"],
        "member_id": historical_run["member_id"],
        "comparison": {
            "basis": "time-slice",
            "baseline": historical_payload["run"]["period_label"],
            "future": future_payload["run"]["period_label"],
            "scenario": future_payload["run"]["experiment_id"],
        },
        "capabilities": {
            "available_metrics": sorted(
                set(historical_payload.get("capabilities", {}).get("available_metrics", []))
                & set(future_payload.get("capabilities", {}).get("available_metrics", []))
            ),
            "metric_count": len(
                set(historical_payload.get("capabilities", {}).get("available_metrics", []))
                & set(future_payload.get("capabilities", {}).get("available_metrics", []))
            ),
            "precipitation_impacts": False,
        },
        "historical": {
            "run": historical_run,
            "coverage": historical_payload["coverage"],
            "qa": historical_payload.get("qa"),
            **copied["historical"],
        },
        "future": {
            "run": future_run,
            "coverage": future_payload["coverage"],
            "qa": future_payload.get("qa"),
            **copied["future"],
        },
        "change": copied["change"],
    }
    existing_pairs: list[dict[str, Any]] = []
    existing_manifest_path = output_dir / "manifest.json"
    if existing_manifest_path.is_file():
        existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if existing_manifest.get("schema") != INDEX_SCHEMA:
            raise ValueError(f"existing bundle has an incompatible schema: {existing_manifest_path}")
        existing_index_path = output_dir / existing_manifest["index"]["path"]
        if sha256(existing_index_path) != existing_manifest["index"]["sha256"]:
            raise ValueError(f"existing climate index checksum does not match: {existing_index_path}")
        existing_pairs = _load_gzip_json(existing_index_path).get("pairs", [])
    pairs = [pair for pair in existing_pairs if pair.get("id") != pair_id]
    pairs.append(pair_record)
    pairs.sort(
        key=lambda pair: (
            pair["source_label"],
            pair["member_id"],
            pair["future"]["run"]["experiment_id"],
        )
    )
    production_ready = all(
        int(role["coverage"]["years"]) >= 30
        for pair in pairs
        for role in (pair["historical"], pair["future"])
    )
    index = {
        "schema": INDEX_SCHEMA,
        "generated_utc": utc_now(),
        "status": "production-window-awaiting-review" if production_ready else "engineering-canary",
        "pairs": pairs,
        "defaults": {"pair": pair_id, "season": "jjas", "metric": "systems"},
        "interpretation": (
            "Each entry is a single-model paired change; multi-model projection statements "
            "require an ensemble of entries. Production-length bundles remain unavailable to "
            "the browser until their historical-performance screen is reviewed and a combined "
            "one-model-one-vote bundle is approved."
        ),
    }
    raw = json.dumps(index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    index_path = output_dir / f"climate-index.{digest[:12]}.json.gz"
    atomic_gzip_json(index_path, index)
    atomic_json(
        output_dir / "manifest.json",
        {
            "schema": INDEX_SCHEMA,
            "generated_utc": index["generated_utc"],
            "index": {
                "path": index_path.name,
                "sha256": sha256(index_path),
                "bytes": index_path.stat().st_size,
            },
            "pairs": len(index["pairs"]),
        },
    )
    return index_path


def _browser_asset(output_dir: Path, prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    path = output_dir / "assets" / f"{prefix}.{digest[:12]}.json.gz"
    atomic_gzip_json(path, payload)
    return {
        "url": f"assets/{path.name}",
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def _load_public_index(manifest_path: Path) -> tuple[dict[str, Any], Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"unsupported climate index manifest: {manifest_path}")
    index_path = Path(manifest["index"]["path"])
    if not index_path.is_absolute():
        index_path = manifest_path.resolve().parent / index_path
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError(f"climate index checksum does not match: {index_path}")
    index = _load_gzip_json(index_path)
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"unsupported climate index asset: {index_path}")
    return index, manifest_path.resolve().parent


def assemble_ensemble(
    public_manifests: list[Path],
    output_dir: Path,
    *,
    include_pair_ids: list[str] | None = None,
    status: str = "multi-model-awaiting-review",
    review_file: Path | None = None,
) -> Path:
    """Merge production pairs and add an equal-weight multi-model comparison."""
    if status not in {"multi-model-awaiting-review", "validated-production-window"}:
        raise ValueError(f"unsupported ensemble status: {status}")
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_pairs: dict[str, dict[str, Any]] = {}
    payloads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for public_manifest in public_manifests:
        index, source_root = _load_public_index(public_manifest)
        for original in index.get("pairs", []):
            if original.get("kind") == "multi-model":
                continue
            pair_id = str(original["id"])
            record = json.loads(json.dumps(original))
            role_payloads: dict[str, dict[str, Any]] = {}
            for role in ("historical", "future", "change"):
                source = source_root / record[role]["url"]
                if sha256(source) != record[role]["sha256"]:
                    raise ValueError(f"{role} checksum does not match for {pair_id}")
                destination = output_dir / "assets" / source.name
                _copy_atomic(source, destination)
                record[role]["url"] = f"assets/{destination.name}"
                role_payloads[role] = _load_gzip_json(source)
            if role_payloads["historical"].get("schema") != SCHEMA:
                raise ValueError(f"historical payload has an unsupported schema for {pair_id}")
            if role_payloads["future"].get("schema") != SCHEMA:
                raise ValueError(f"future payload has an unsupported schema for {pair_id}")
            if role_payloads["change"].get("schema") != PAIR_SCHEMA:
                raise ValueError(f"change payload has an unsupported schema for {pair_id}")
            existing = copied_pairs.get(pair_id)
            if existing is not None and existing != record:
                raise ValueError(f"conflicting definitions for CMIP6 pair {pair_id}")
            copied_pairs[pair_id] = record
            payloads[pair_id] = (role_payloads["historical"], role_payloads["future"])

    requested = list(dict.fromkeys(include_pair_ids or sorted(copied_pairs)))
    missing = sorted(set(requested) - set(copied_pairs))
    if missing:
        raise ValueError(f"requested CMIP6 pairs are absent: {missing}")
    production_ids = [
        pair_id
        for pair_id in requested
        if all(
            int(copied_pairs[pair_id][role]["coverage"]["years"]) >= 30
            for role in ("historical", "future")
        )
    ]
    ineligible = sorted(set(requested) - set(production_ids))
    if ineligible:
        raise ValueError(f"requested pairs do not each contain two 30-year windows: {ineligible}")
    if len(production_ids) < 2:
        raise ValueError("at least two 30-year model pairs are required for a multi-model product")
    source_labels = [str(copied_pairs[pair_id]["source_label"]) for pair_id in production_ids]
    if len(set(source_labels)) != len(source_labels):
        raise ValueError("one-model-one-vote assembly currently requires one member per source model")
    scenarios = {
        str(copied_pairs[pair_id]["future"]["run"]["experiment_id"])
        for pair_id in production_ids
    }
    if len(scenarios) != 1:
        raise ValueError(f"multi-model assembly mixes future experiments: {sorted(scenarios)}")
    scenario = scenarios.pop()
    historical_payloads = [payloads[pair_id][0] for pair_id in production_ids]
    future_payloads = [payloads[pair_id][1] for pair_id in production_ids]
    historical = aggregate_run_payloads(
        historical_payloads, role="historical", model_ids=production_ids
    )
    future = aggregate_run_payloads(future_payloads, role="future", model_ids=production_ids)
    change = aggregate_change_payloads(
        historical_payloads, future_payloads, model_ids=production_ids
    )
    ensemble_id = hashlib.sha256(
        ("one-model-one-vote|" + scenario + "|" + "|".join(sorted(production_ids))).encode("utf-8")
    ).hexdigest()[:16]
    screen_records: list[dict[str, Any]] = []
    for pair_id in production_ids:
        qa = copied_pairs[pair_id].get("historical", {}).get("qa") or {}
        screen = qa.get("historical_screen") or {}
        screen_records.append(
            {
                "id": pair_id,
                "source_label": copied_pairs[pair_id]["source_label"],
                "status": screen.get("screening_status"),
                "diagnostic_flags": screen.get("diagnostic_flags"),
                "comparisons": screen.get("comparisons"),
                "model": screen.get("model"),
                "reference_metrics": screen.get("reference_metrics"),
                "classification": screen.get("classification_screen"),
                "jjas": {
                    "status": (screen.get("seasonal", {}).get("jjas") or {}).get(
                        "screening_status"
                    ),
                    "diagnostic_flags": (
                        screen.get("seasonal", {}).get("jjas") or {}
                    ).get("diagnostic_flags"),
                    "comparisons": (screen.get("seasonal", {}).get("jjas") or {}).get(
                        "comparisons"
                    ),
                    "model": (screen.get("seasonal", {}).get("jjas") or {}).get("model"),
                    "reference_metrics": (
                        screen.get("seasonal", {}).get("jjas") or {}
                    ).get("reference_metrics"),
                },
            }
        )
    historical["qa"]["historical_screening"] = screen_records
    paired_metric_counts = {
        metric: sum(
            metric in set(left.get("capabilities", {}).get("available_metrics", CHANGE_METRICS))
            and metric in set(right.get("capabilities", {}).get("available_metrics", CHANGE_METRICS))
            for left, right in zip(historical_payloads, future_payloads, strict=True)
        )
        for metric in CHANGE_METRICS
    }
    paired_available_metrics = [
        metric for metric, count in paired_metric_counts.items() if count > 0
    ]
    ensemble_record = {
        "id": ensemble_id,
        "kind": "multi-model",
        "comparison_basis": "time-slice",
        "label": f"Multi-model mean · {scenario.upper()} · {len(production_ids)} models",
        "source_label": "Multi-model mean",
        "member_id": "one-model-one-vote",
        "model_ids": production_ids,
        "comparison": {
            "basis": "time-slice",
            "baseline": historical["run"]["period_label"],
            "future": future["run"]["period_label"],
            "scenario": scenario,
        },
        "capabilities": {
            "available_metrics": paired_available_metrics,
            "metric_model_counts": paired_metric_counts,
            "metric_count": len(paired_available_metrics),
            "precipitation_impacts": False,
        },
        "historical": {
            "run": historical["run"],
            "coverage": historical["coverage"],
            "qa": historical["qa"],
            **_browser_asset(output_dir, "climate-ensemble-historical", historical),
        },
        "future": {
            "run": future["run"],
            "coverage": future["coverage"],
            "qa": future["qa"],
            **_browser_asset(output_dir, "climate-ensemble-future", future),
        },
        "change": _browser_asset(output_dir, "climate-ensemble-change", change),
    }

    review: dict[str, Any] | None = None
    if status == "validated-production-window":
        if review_file is None:
            raise ValueError("validated ensemble publication requires an explicit review file")
        review = json.loads(review_file.read_text(encoding="utf-8"))
        if review.get("schema") != REVIEW_SCHEMA or review.get("status") != "approved":
            raise ValueError("ensemble review is absent or not approved")
        reviewed_ids = sorted(str(value) for value in review.get("included_pair_ids", []))
        if reviewed_ids != sorted(production_ids):
            raise ValueError("ensemble review does not name the exact included pair IDs")
        if not review.get("approved_by") or not review.get("approved_utc"):
            raise ValueError("ensemble review requires approver and timestamp")

    pairs = [copied_pairs[pair_id] for pair_id in sorted(copied_pairs)] + [ensemble_record]
    index = {
        "schema": INDEX_SCHEMA,
        "generated_utc": utc_now(),
        "status": status,
        "pairs": pairs,
        "defaults": {"pair": ensemble_id, "season": "jjas", "metric": "systems"},
        "ensemble": {
            "id": ensemble_id,
            "aggregation": "one_model_one_vote",
            "model_count": len(production_ids),
            "included_pair_ids": production_ids,
            "historical_screening": screen_records,
        },
        "review": (
            {
                "path": review_file.name,
                "sha256": sha256(review_file),
                "approved_by": review["approved_by"],
                "approved_utc": review["approved_utc"],
            }
            if review is not None and review_file is not None else None
        ),
        "interpretation": (
            "Each source model has equal weight. Within-model year resampling and across-model "
            "spread are reported separately; detector and intensity thresholds remain frozen."
        ),
    }
    raw = json.dumps(index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    index_path = output_dir / f"climate-index.{digest[:12]}.json.gz"
    atomic_gzip_json(index_path, index)
    atomic_json(
        output_dir / "manifest.json",
        {
            "schema": INDEX_SCHEMA,
            "generated_utc": index["generated_utc"],
            "status": status,
            "index": {
                "path": index_path.name,
                "sha256": sha256(index_path),
                "bytes": index_path.stat().st_size,
            },
            "pairs": len(pairs),
            "models": len(production_ids),
            "included_pair_ids": production_ids,
            "review_sha256": sha256(review_file) if review_file is not None else None,
        },
    )
    return index_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--catalogue", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--source-label", required=True)
    run.add_argument("--experiment-id", required=True)
    run.add_argument("--member-id", required=True)
    run.add_argument("--period-label", required=True)
    run.add_argument("--start-year", type=int)
    run.add_argument("--end-year", type=int)
    run.add_argument("--qa-report", type=Path)
    planned = subparsers.add_parser("run-from-plan")
    planned.add_argument("--catalogue", type=Path, required=True)
    planned.add_argument("--period-plan", type=Path, required=True)
    planned.add_argument("--output-dir", type=Path, required=True)
    planned.add_argument("--qa-report", type=Path)
    pair = subparsers.add_parser("pair")
    pair.add_argument("--historical-manifest", type=Path, required=True)
    pair.add_argument("--future-manifest", type=Path, required=True)
    pair.add_argument("--output-dir", type=Path, required=True)
    publish = subparsers.add_parser("publish-pair")
    publish.add_argument("--historical-manifest", type=Path, required=True)
    publish.add_argument("--future-manifest", type=Path, required=True)
    publish.add_argument("--pair-manifest", type=Path, required=True)
    publish.add_argument("--output-dir", type=Path, required=True)
    ensemble = subparsers.add_parser("ensemble-index")
    ensemble.add_argument("--public-manifest", type=Path, action="append", required=True)
    ensemble.add_argument("--output-dir", type=Path, required=True)
    ensemble.add_argument("--include-pair", action="append")
    ensemble.add_argument(
        "--status",
        choices=("multi-model-awaiting-review", "validated-production-window"),
        default="multi-model-awaiting-review",
    )
    ensemble.add_argument("--review-file", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "run":
        path = summarise_run(
            args.catalogue,
            args.output_dir,
            source_label=args.source_label,
            experiment_id=args.experiment_id,
            member_id=args.member_id,
            period_label=args.period_label,
            start_year=args.start_year,
            end_year=args.end_year,
            qa_report=args.qa_report,
        )
    elif args.command == "run-from-plan":
        plan = json.loads(args.period_plan.read_text(encoding="utf-8"))
        run = plan["run"]
        path = summarise_run(
            args.catalogue,
            args.output_dir,
            source_label=run["source_id"],
            experiment_id=run["experiment_id"],
            member_id=run["member_id"],
            period_label=f"{plan['core_start'][:4]}–{plan['core_end'][:4]}",
            start_year=int(plan["core_start"][:4]),
            end_year=int(plan["core_end"][:4]),
            qa_report=args.qa_report,
        )
    elif args.command == "pair":
        path = summarise_pair(args.historical_manifest, args.future_manifest, args.output_dir)
    elif args.command == "publish-pair":
        path = publish_pair(
            args.historical_manifest,
            args.future_manifest,
            args.pair_manifest,
            args.output_dir,
        )
    else:
        path = assemble_ensemble(
            args.public_manifest,
            args.output_dir,
            include_pair_ids=args.include_pair,
            status=args.status,
            review_file=args.review_file,
        )
    print(path)


if __name__ == "__main__":
    main()
