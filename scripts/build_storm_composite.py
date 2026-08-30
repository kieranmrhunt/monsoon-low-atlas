#!/usr/bin/env python3
"""Build lazy, per-system storm-centred composite assets for the web atlas.

The horizontal footprint is a mean of UTC-day precipitation accumulations on
an unrotated storm-relative grid.  ERA5 and IMERG use the same daily centres
and target grid.  The vertical product is a zonal section at relative latitude
zero, averaged over nine equally spaced lifecycle snapshots.

For 1979 onward, vertical fields preferentially use the local JASMIN ERA5
model-level archive. Earlier fields, and snapshots absent from that local
archive, use the public ARCO ERA5 pressure-level store. Per-system provenance
retains the source split for the renderer.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from netCDF4 import Dataset
from numcodecs import get_codec
from scipy.ndimage import map_coordinates


SCHEMA = "monsoon-low-atlas-storm-composite-v1"
RELATIVE_DEGREES = np.arange(-10.0, 10.0001, 0.25, dtype=np.float64)
PRESSURE_HPA = np.array(
    [
        1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750, 700,
        650, 600, 550, 500, 450, 400, 350, 300, 250, 225, 200, 175,
        150, 125, 100,
    ],
    dtype=np.float64,
)
N_LIFECYCLE_SNAPSHOTS = 9
EARTH_RADIUS_M = 6_371_000.0
GRID_STEP_RADIANS = math.radians(0.25)
ARCO_EPOCH = pd.Timestamp("1900-01-01T00:00:00")
ARCO_BASE = (
    "https://storage.googleapis.com/gcp-public-data-arco-era5/ar/"
    "full_37-1h-0p25deg-chunk-1.zarr-v3"
)
ARCO_LEVELS = np.array(
    [
        1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175,
        200, 225, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700,
        750, 775, 800, 825, 850, 875, 900, 925, 950, 975, 1000,
    ],
    dtype=np.int64,
)
ARCO_TARGET_INDICES = np.array(
    [int(np.where(ARCO_LEVELS == level)[0][0]) for level in PRESSURE_HPA],
    dtype=np.int64,
)
ARCO_SHAPE = (37, 721, 1440)
ARCO_CODEC = get_codec(
    {"id": "blosc", "cname": "lz4", "clevel": 5, "shuffle": 1, "blocksize": 0}
)

# Selected ERA5 L137 full levels bracketing the 27 output pressure levels.
# a/b are the half-level coefficients immediately above and below each full
# level.  Source: ECMWF's official L137 model-level definition table.
MODEL_LEVEL_DEFINITIONS = np.array(
    [
        (60, 9562.682617, 0.000199, 10065.978516, 0.000340),
        (65, 12211.547852, 0.001992, 12766.873047, 0.002857),
        (68, 13881.331055, 0.005378, 14432.139648, 0.007133),
        (71, 15508.256836, 0.011806, 16026.115234, 0.014816),
        (74, 17008.789063, 0.022355, 17467.613281, 0.026964),
        (77, 18308.433594, 0.038026, 18685.718750, 0.044548),
        (79, 19031.289063, 0.051773, 19343.511719, 0.059728),
        (83, 20059.931641, 0.088286, 20219.664063, 0.099462),
        (87, 20442.078125, 0.138313, 20425.718750, 0.153125),
        (90, 20249.511719, 0.185689, 20087.085938, 0.203491),
        (93, 19608.572266, 0.242244, 19290.226563, 0.263242),
        (96, 18489.707031, 0.308598, 18006.925781, 0.332939),
        (98, 17471.839844, 0.358254, 16888.687500, 0.384363),
        (100, 16262.046875, 0.411125, 15596.695313, 0.438391),
        (103, 14173.324219, 0.493800, 13427.769531, 0.521619),
        (105, 12668.257813, 0.549301, 11901.339844, 0.576692),
        (108, 10370.175781, 0.630036, 9617.515625, 0.655736),
        (109, 9617.515625, 0.655736, 8880.453125, 0.680643),
        (111, 8163.375000, 0.704669, 7470.343750, 0.727739),
        (112, 7470.343750, 0.727739, 6804.421875, 0.749797),
        (114, 6168.531250, 0.770798, 5564.382813, 0.790717),
        (116, 4993.796875, 0.809536, 4457.375000, 0.827256),
        (118, 3955.960938, 0.843881, 3489.234375, 0.859432),
        (120, 3057.265625, 0.873929, 2659.140625, 0.887408),
        (123, 1961.500000, 0.911448, 1659.476563, 0.922096),
        (127, 926.507813, 0.949064, 734.992188, 0.956550),
        (133, 122.101563, 0.984542, 62.781250, 0.988500),
        (137, 0.000000, 0.997630, 0.000000, 1.000000),
    ],
    dtype=np.float64,
)
MODEL_LEVELS = MODEL_LEVEL_DEFINITIONS[:, 0].astype(np.int64)
MODEL_LEVEL_INDICES = MODEL_LEVELS - 1
MODEL_A_FULL = 0.5 * (
    MODEL_LEVEL_DEFINITIONS[:, 1] + MODEL_LEVEL_DEFINITIONS[:, 3]
)
MODEL_B_FULL = 0.5 * (
    MODEL_LEVEL_DEFINITIONS[:, 2] + MODEL_LEVEL_DEFINITIONS[:, 4]
)


@dataclass(frozen=True)
class SourcePaths:
    era5_precip: Path
    imerg_daily: Path
    badc_model: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite_mean(total: np.ndarray, count: np.ndarray) -> np.ndarray:
    output = np.full(total.shape, np.nan, dtype=np.float64)
    np.divide(total, count, out=output, where=count > 0)
    return output


def add_field(total: np.ndarray, count: np.ndarray, field: np.ndarray) -> None:
    valid = np.isfinite(field)
    total[valid] += field[valid]
    count[valid] += 1


def pack_field(
    field: np.ndarray,
    *,
    scale: float,
    units: str,
    samples: int,
    requested_samples: int,
    source: str,
) -> dict[str, Any] | None:
    field = np.asarray(field, dtype=np.float64)
    finite = np.isfinite(field)
    if not finite.any() or samples <= 0:
        return None
    quantised = np.zeros(field.shape, dtype=np.int32)
    quantised[finite] = np.rint(field[finite] / scale).astype(np.int32)
    flattened: list[int | None] = [
        int(value) if valid else None
        for value, valid in zip(quantised.ravel(), finite.ravel(), strict=True)
    ]
    return {
        "shape": list(field.shape),
        "scale": scale,
        "data": flattened,
        "units": units,
        "samples": int(samples),
        "requested_samples": int(requested_samples),
        "availability_fraction": round(samples / max(1, requested_samples), 4),
        "spatial_coverage_fraction": round(float(finite.mean()), 4),
        "min": round(float(np.nanmin(field)), 4),
        "max": round(float(np.nanmax(field)), 4),
        "source": source,
    }


def track_rows(catalogue: Path, track_id: int) -> pd.DataFrame:
    frame = pd.read_parquet(
        catalogue,
        columns=["track_id", "time", "lat", "lon"],
        filters=[("track_id", "=", int(track_id))],
    )
    if frame.empty:
        # Some Parquet engines cannot prune this file's row groups by track_id.
        frame = pd.read_parquet(
            catalogue, columns=["track_id", "time", "lat", "lon"]
        )
        frame = frame.loc[frame["track_id"] == int(track_id)]
    if frame.empty:
        raise ValueError(f"track_id {track_id} is not present in {catalogue}")
    frame = frame.sort_values("time", kind="stable").reset_index(drop=True)
    frame["time"] = pd.to_datetime(frame["time"])
    return frame


def catalogue_track_ids(catalogue: Path) -> np.ndarray:
    ids = pd.read_parquet(catalogue, columns=["track_id"])["track_id"]
    return np.sort(ids.drop_duplicates().astype(np.int64).to_numpy())


def daily_centres(track: pd.DataFrame) -> list[tuple[pd.Timestamp, float, float]]:
    days = track["time"].dt.normalize().drop_duplicates().sort_values()
    output: list[tuple[pd.Timestamp, float, float]] = []
    for day in days:
        within = track.loc[track["time"].dt.normalize() == day]
        noon = day + pd.Timedelta(hours=12)
        chosen = within.iloc[int(np.argmin(np.abs(within["time"] - noon)))]
        output.append((pd.Timestamp(day), float(chosen["lat"]), float(chosen["lon"])))
    return output


def lifecycle_centres(track: pd.DataFrame) -> list[tuple[pd.Timestamp, float, float]]:
    indexes = np.unique(
        np.rint(np.linspace(0, len(track) - 1, N_LIFECYCLE_SNAPSHOTS)).astype(int)
    )
    output = []
    for index in indexes:
        row = track.iloc[int(index)]
        output.append((pd.Timestamp(row["time"]), float(row["lat"]), float(row["lon"])))
    return output


def sample_regular_grid(
    field: np.ndarray,
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    target_latitudes: np.ndarray,
    target_longitudes: np.ndarray,
) -> np.ndarray:
    """Bilinearly sample a regular lat/lon field, returning lat x lon."""
    latitudes = np.asarray(latitudes, dtype=np.float64)
    longitudes = np.asarray(longitudes, dtype=np.float64)
    field = np.asarray(np.ma.filled(field, np.nan), dtype=np.float64)
    if latitudes[0] > latitudes[-1]:
        latitudes = latitudes[::-1]
        field = field[::-1, :]
    if longitudes[0] > longitudes[-1]:
        longitudes = longitudes[::-1]
        field = field[:, ::-1]
    lat_step = float(np.median(np.diff(latitudes)))
    lon_step = float(np.median(np.diff(longitudes)))
    yy, xx = np.meshgrid(target_latitudes, target_longitudes, indexing="ij")
    coordinates = np.vstack(
        [
            ((yy - latitudes[0]) / lat_step).ravel(),
            ((xx - longitudes[0]) / lon_step).ravel(),
        ]
    )
    sampled = map_coordinates(
        field,
        coordinates,
        order=1,
        mode="constant",
        cval=np.nan,
        prefilter=False,
    )
    return sampled.reshape(yy.shape)


def era5_daily_footprint(
    paths: SourcePaths,
    day: pd.Timestamp,
    centre_lat: float,
    centre_lon: float,
) -> np.ndarray | None:
    source = paths.era5_precip / f"{day:%Y%m}.nc"
    if not source.exists():
        return None
    with Dataset(source) as dataset:
        latitude = np.asarray(dataset.variables["latitude"][:], dtype=np.float64)
        longitude = np.asarray(dataset.variables["longitude"][:], dtype=np.float64)
        rate_name = next(
            (name for name in ("mtpr", "avg_tprate") if name in dataset.variables),
            None,
        )
        if rate_name is None:
            raise KeyError("ERA5 precipitation rate (mtpr or avg_tprate)")
        rate_variable = dataset.variables[rate_name]
        first_hour = (int(day.day) - 1) * 24
        last_hour = first_hour + 24
        if last_hour > rate_variable.shape[0]:
            return None
        lat_mask = (latitude >= centre_lat - 10.5) & (latitude <= centre_lat + 10.5)
        lon_mask = (longitude >= centre_lon - 10.5) & (longitude <= centre_lon + 10.5)
        lat_indices = np.flatnonzero(lat_mask)
        lon_indices = np.flatnonzero(lon_mask)
        if len(lat_indices) < 2 or len(lon_indices) < 2:
            return None
        rate = rate_variable[
            first_hour:last_hour,
            lat_indices[0] : lat_indices[-1] + 1,
            lon_indices[0] : lon_indices[-1] + 1,
        ]
        daily = np.asarray(np.ma.filled(rate, np.nan), dtype=np.float64).sum(axis=0) * 3600.0
        daily = np.maximum(daily, 0.0)
        return sample_regular_grid(
            daily,
            latitude[lat_indices[0] : lat_indices[-1] + 1],
            longitude[lon_indices[0] : lon_indices[-1] + 1],
            centre_lat + RELATIVE_DEGREES,
            centre_lon + RELATIVE_DEGREES,
        )


def imerg_file(root: Path, day: pd.Timestamp) -> Path | None:
    year_directory = root / f"{day:%Y}"
    matches = sorted(year_directory.glob(f"*{day:%Y%m%d}*"))
    return matches[0] if matches else None


def imerg_daily_footprint(
    paths: SourcePaths,
    day: pd.Timestamp,
    centre_lat: float,
    centre_lon: float,
) -> np.ndarray | None:
    source = imerg_file(paths.imerg_daily, day)
    if source is None:
        return None
    with Dataset(source) as dataset:
        latitude = np.asarray(dataset.variables["lat"][:], dtype=np.float64)
        longitude = np.asarray(dataset.variables["lon"][:], dtype=np.float64)
        precipitation_name = next(
            (
                name
                for name in ("precipitationCal", "precipitation")
                if name in dataset.variables
            ),
            None,
        )
        if precipitation_name is None:
            raise KeyError("IMERG precipitation (precipitationCal or precipitation)")
        raw = dataset.variables[precipitation_name][0, :, :]
        daily = np.asarray(np.ma.filled(raw, np.nan), dtype=np.float64).T
        daily[daily < 0] = np.nan
        lat_mask = (latitude >= centre_lat - 10.5) & (latitude <= centre_lat + 10.5)
        lon_mask = (longitude >= centre_lon - 10.5) & (longitude <= centre_lon + 10.5)
        lat_indices = np.flatnonzero(lat_mask)
        lon_indices = np.flatnonzero(lon_mask)
        if len(lat_indices) < 2 or len(lon_indices) < 2:
            return None
        return sample_regular_grid(
            daily[np.ix_(lat_indices, lon_indices)],
            latitude[lat_indices],
            longitude[lon_indices],
            centre_lat + RELATIVE_DEGREES,
            centre_lon + RELATIVE_DEGREES,
        )


def build_precipitation(
    track: pd.DataFrame,
    paths: SourcePaths,
) -> tuple[dict[str, Any], list[str]]:
    centres = daily_centres(track)
    shape = (len(RELATIVE_DEGREES), len(RELATIVE_DEGREES))
    totals = {
        "era5": np.zeros(shape, dtype=np.float64),
        "imerg": np.zeros(shape, dtype=np.float64),
    }
    counts = {
        "era5": np.zeros(shape, dtype=np.int16),
        "imerg": np.zeros(shape, dtype=np.int16),
    }
    sample_counts = {"era5": 0, "imerg": 0}
    errors: list[str] = []
    for day, centre_lat, centre_lon in centres:
        for key, reader in (
            ("era5", era5_daily_footprint),
            ("imerg", imerg_daily_footprint),
        ):
            try:
                field = reader(paths, day, centre_lat, centre_lon)
            except Exception as error:  # preserve the other source on a bad day
                errors.append(f"{key} precipitation {day:%Y-%m-%d}: {error}")
                continue
            if field is None or not np.isfinite(field).any():
                continue
            add_field(totals[key], counts[key], field)
            sample_counts[key] += 1
    requested = len(centres)
    fields: dict[str, Any] = {}
    for key, label in (
        ("era5", "ERA5 mean total precipitation rate, accumulated over UTC days"),
        (
            "imerg",
            "IMERG Final Run daily precipitation (V06 precipitationCal / V07 precipitation)",
        ),
    ):
        packed = pack_field(
            finite_mean(totals[key], counts[key]),
            scale=0.1,
            units="mm day-1",
            samples=sample_counts[key],
            requested_samples=requested,
            source=label,
        )
        if packed is not None:
            fields[key] = packed
    return fields, errors


def regular_line_from_global(
    field: np.ndarray,
    latitude: float,
    target_longitudes: np.ndarray,
) -> np.ndarray:
    """Sample [level, 721, 1440] ERA5 data at one latitude."""
    latitude_position = (90.0 - float(latitude)) / 0.25
    lat0 = int(np.floor(latitude_position))
    lat0 = min(max(lat0, 0), field.shape[1] - 2)
    lat_weight = latitude_position - lat0
    longitude_position = np.mod(target_longitudes, 360.0) / 0.25
    lon0 = np.floor(longitude_position).astype(np.int64) % field.shape[2]
    lon1 = (lon0 + 1) % field.shape[2]
    lon_weight = longitude_position - np.floor(longitude_position)
    south_north_0 = (1.0 - lat_weight) * field[:, lat0, lon0] + lat_weight * field[:, lat0 + 1, lon0]
    south_north_1 = (1.0 - lat_weight) * field[:, lat0, lon1] + lat_weight * field[:, lat0 + 1, lon1]
    return (1.0 - lon_weight) * south_north_0 + lon_weight * south_north_1


def arco_chunk(variable: str, timestamp: pd.Timestamp, retries: int = 5) -> np.ndarray:
    hour_index = int((timestamp - ARCO_EPOCH) / pd.Timedelta(hours=1))
    url = f"{ARCO_BASE}/{variable}/{hour_index}.0.0.0"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "monsoon-low-atlas/1"})
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
            decoded = ARCO_CODEC.decode(payload)
            array = np.frombuffer(decoded, dtype="<f4")
            if array.size != int(np.prod(ARCO_SHAPE)):
                raise ValueError(f"unexpected decoded shape for {variable}: {array.size}")
            return array.reshape(ARCO_SHAPE)
        except (OSError, urllib.error.URLError, ValueError) as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(min(45.0, 4.0 * (2**attempt)) + random.random() * 2.0)
    raise RuntimeError(f"ARCO fetch failed after {retries} attempts: {url}: {last_error}")


def theta_e_bolton(temperature_k: np.ndarray, specific_humidity: np.ndarray) -> np.ndarray:
    """Bolton (1980) equivalent potential temperature, matching MetPy."""
    pressure = PRESSURE_HPA[:, None]
    temperature = np.asarray(temperature_k, dtype=np.float64)
    q = np.clip(np.asarray(specific_humidity, dtype=np.float64), 1.0e-8, 0.08)
    mixing_ratio = q / (1.0 - q)
    epsilon = 0.62195691
    vapour_pressure = pressure * mixing_ratio / (epsilon + mixing_ratio)
    log_ratio = np.log(np.maximum(vapour_pressure, 1.0e-8) / 6.112)
    dewpoint = 273.15 + 243.5 * log_ratio / (17.67 - log_ratio)
    lcl_temperature = 56.0 + 1.0 / (
        1.0 / (dewpoint - 56.0) + np.log(temperature / dewpoint) / 800.0
    )
    theta_l = (
        temperature
        * (1000.0 / (pressure - vapour_pressure)) ** 0.2857142857142857
        * (temperature / lcl_temperature) ** (0.28 * mixing_ratio)
    )
    theta_e = theta_l * np.exp(
        mixing_ratio
        * (1.0 + 0.448 * mixing_ratio)
        * (3036.0 / lcl_temperature - 1.78)
    )
    invalid = ~np.isfinite(temperature_k) | ~np.isfinite(specific_humidity)
    theta_e[invalid] = np.nan
    theta_e[(theta_e < 200.0) | (theta_e > 500.0)] = np.nan
    return theta_e


def relative_humidity_mixed_phase(
    temperature_k: np.ndarray,
    specific_humidity: np.ndarray,
) -> np.ndarray:
    """Relative humidity from ERA5 T and q using IFS-style mixed-phase saturation."""
    pressure_pa = PRESSURE_HPA[:, None] * 100.0
    temperature = np.asarray(temperature_k, dtype=np.float64)
    q = np.asarray(specific_humidity, dtype=np.float64)
    epsilon = 0.622
    vapour_pressure = q * pressure_pa / (epsilon + (1.0 - epsilon) * q)
    temperature_c = temperature - 273.15
    saturation_water = 611.21 * np.exp(
        (18.678 - temperature_c / 234.5)
        * (temperature_c / (257.14 + temperature_c))
    )
    saturation_ice = 611.15 * np.exp(
        (23.036 - temperature_c / 333.7)
        * (temperature_c / (279.82 + temperature_c))
    )
    water_weight = np.clip((temperature - 250.16) / 23.0, 0.0, 1.0) ** 2
    saturation_pressure = (
        water_weight * saturation_water + (1.0 - water_weight) * saturation_ice
    )
    relative_humidity = 100.0 * vapour_pressure / saturation_pressure
    invalid = (
        ~np.isfinite(temperature)
        | ~np.isfinite(q)
        | ~np.isfinite(relative_humidity)
        | (saturation_pressure <= 0.0)
    )
    relative_humidity = np.clip(relative_humidity, 0.0, 100.0)
    relative_humidity[invalid] = np.nan
    return relative_humidity


def arco_vertical_snapshot(
    timestamp: pd.Timestamp,
    centre_lat: float,
    centre_lon: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    target_longitudes = centre_lon + RELATIVE_DEGREES
    temperature_global = arco_chunk("temperature", timestamp)
    temperature = regular_line_from_global(
        temperature_global[ARCO_TARGET_INDICES], centre_lat, target_longitudes
    )
    del temperature_global
    humidity_global = arco_chunk("specific_humidity", timestamp)
    humidity = regular_line_from_global(
        humidity_global[ARCO_TARGET_INDICES], centre_lat, target_longitudes
    )
    del humidity_global

    # Relative vorticity from pressure-level winds on the sphere.  Central
    # differences use the native 0.25-degree grid around the requested line.
    u_global = arco_chunk("u_component_of_wind", timestamp)
    u_levels = u_global[ARCO_TARGET_INDICES]
    u_north = regular_line_from_global(u_levels, centre_lat + 0.25, target_longitudes)
    u_south = regular_line_from_global(u_levels, centre_lat - 0.25, target_longitudes)
    u_centre = regular_line_from_global(u_levels, centre_lat, target_longitudes)
    del u_levels, u_global
    v_global = arco_chunk("v_component_of_wind", timestamp)
    v_levels = v_global[ARCO_TARGET_INDICES]
    v_east = regular_line_from_global(v_levels, centre_lat, target_longitudes + 0.25)
    v_west = regular_line_from_global(v_levels, centre_lat, target_longitudes - 0.25)
    del v_levels, v_global
    latitude_radians = math.radians(centre_lat)
    relative_vorticity = (
        (v_east - v_west)
        / (2.0 * GRID_STEP_RADIANS * EARTH_RADIUS_M * math.cos(latitude_radians))
        - (u_north - u_south) / (2.0 * GRID_STEP_RADIANS * EARTH_RADIUS_M)
        + u_centre * math.tan(latitude_radians) / EARTH_RADIUS_M
    )
    theta_e = theta_e_bolton(temperature, humidity)
    relative_humidity = relative_humidity_mixed_phase(temperature, humidity)
    return relative_vorticity * 1.0e5, theta_e, relative_humidity


def arco_relative_humidity_snapshot(
    timestamp: pd.Timestamp,
    centre_lat: float,
    centre_lon: float,
) -> np.ndarray:
    """Read only the thermodynamic ARCO fields needed for an RH augmentation."""
    target_longitudes = centre_lon + RELATIVE_DEGREES
    temperature_global = arco_chunk("temperature", timestamp)
    temperature = regular_line_from_global(
        temperature_global[ARCO_TARGET_INDICES], centre_lat, target_longitudes
    )
    del temperature_global
    humidity_global = arco_chunk("specific_humidity", timestamp)
    humidity = regular_line_from_global(
        humidity_global[ARCO_TARGET_INDICES], centre_lat, target_longitudes
    )
    del humidity_global
    return relative_humidity_mixed_phase(temperature, humidity)


def model_file(root: Path, timestamp: pd.Timestamp, variable: str) -> Path:
    return (
        root
        / f"{timestamp:%Y}"
        / f"{timestamp:%m}"
        / f"{timestamp:%d}"
        / f"ecmwf-era5_oper_an_ml_{timestamp:%Y%m%d%H%M}.{variable}.nc"
    )


def read_model_line(
    source: Path,
    variable_name: str,
    level_indices: np.ndarray | None,
    centre_lat: float,
    target_longitudes: np.ndarray,
) -> np.ndarray:
    latitude_position = (90.0 - centre_lat) / 0.25
    lat0 = min(max(int(np.floor(latitude_position)), 0), 719)
    lat_weight = latitude_position - lat0
    longitude_position = np.mod(target_longitudes, 360.0) / 0.25
    lon0 = np.floor(longitude_position).astype(np.int64)
    lon_weight = longitude_position - lon0
    lon_min = int(lon0.min())
    lon_max = int(lon0.max()) + 1
    if lon_min < 0 or lon_max >= 1440:
        raise ValueError("wrapped longitudes are not expected in the atlas domain")
    with Dataset(source) as dataset:
        variable = dataset.variables[variable_name]
        if level_indices is None:
            raw = variable[0, lat0 : lat0 + 2, lon_min : lon_max + 1]
            data = np.asarray(np.ma.filled(raw, np.nan), dtype=np.float64)[None, :, :]
        else:
            raw = variable[
                0,
                level_indices,
                lat0 : lat0 + 2,
                lon_min : lon_max + 1,
            ]
            data = np.asarray(np.ma.filled(raw, np.nan), dtype=np.float64)
    local_lon0 = lon0 - lon_min
    at_lon0 = (1.0 - lat_weight) * data[:, 0, local_lon0] + lat_weight * data[:, 1, local_lon0]
    at_lon1 = (1.0 - lat_weight) * data[:, 0, local_lon0 + 1] + lat_weight * data[:, 1, local_lon0 + 1]
    line = (1.0 - lon_weight) * at_lon0 + lon_weight * at_lon1
    return line[0] if level_indices is None else line


def pressure_interpolate_model_line(
    values: np.ndarray,
    surface_pressure_pa: np.ndarray,
) -> np.ndarray:
    model_pressure_pa = (
        MODEL_A_FULL[:, None] + MODEL_B_FULL[:, None] * surface_pressure_pa[None, :]
    )
    output = np.full((len(PRESSURE_HPA), values.shape[1]), np.nan, dtype=np.float64)
    for column in range(values.shape[1]):
        pressure_column = model_pressure_pa[:, column]
        value_column = values[:, column]
        valid = np.isfinite(pressure_column) & np.isfinite(value_column)
        if valid.sum() < 2:
            continue
        p = pressure_column[valid]
        v = value_column[valid]
        order = np.argsort(p)
        target_pa = PRESSURE_HPA * 100.0
        interpolated = np.interp(target_pa, p[order], v[order], left=np.nan, right=np.nan)
        interpolated[target_pa > surface_pressure_pa[column]] = np.nan
        output[:, column] = interpolated
    return output


def badc_vertical_snapshot(
    root: Path,
    timestamp: pd.Timestamp,
    centre_lat: float,
    centre_lon: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    target_longitudes = centre_lon + RELATIVE_DEGREES
    sources = {key: model_file(root, timestamp, key) for key in ("vo", "t", "q", "lnsp")}
    missing = [str(path) for path in sources.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("; ".join(missing))
    log_surface_pressure = read_model_line(
        sources["lnsp"], "lnsp", None, centre_lat, target_longitudes
    )
    surface_pressure = np.exp(log_surface_pressure)
    vorticity_model = read_model_line(
        sources["vo"], "vo", MODEL_LEVEL_INDICES, centre_lat, target_longitudes
    )
    temperature_model = read_model_line(
        sources["t"], "t", MODEL_LEVEL_INDICES, centre_lat, target_longitudes
    )
    humidity_model = read_model_line(
        sources["q"], "q", MODEL_LEVEL_INDICES, centre_lat, target_longitudes
    )
    vorticity = pressure_interpolate_model_line(vorticity_model, surface_pressure) * 1.0e5
    temperature = pressure_interpolate_model_line(temperature_model, surface_pressure)
    humidity = pressure_interpolate_model_line(humidity_model, surface_pressure)
    return (
        vorticity,
        theta_e_bolton(temperature, humidity),
        relative_humidity_mixed_phase(temperature, humidity),
    )


def badc_relative_humidity_snapshot(
    root: Path,
    timestamp: pd.Timestamp,
    centre_lat: float,
    centre_lon: float,
) -> np.ndarray:
    """Read only local ERA5 T, q and surface pressure for an RH augmentation."""
    target_longitudes = centre_lon + RELATIVE_DEGREES
    sources = {key: model_file(root, timestamp, key) for key in ("t", "q", "lnsp")}
    missing = [str(path) for path in sources.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("; ".join(missing))
    surface_pressure = np.exp(
        read_model_line(sources["lnsp"], "lnsp", None, centre_lat, target_longitudes)
    )
    temperature = pressure_interpolate_model_line(
        read_model_line(
            sources["t"], "t", MODEL_LEVEL_INDICES, centre_lat, target_longitudes
        ),
        surface_pressure,
    )
    humidity = pressure_interpolate_model_line(
        read_model_line(
            sources["q"], "q", MODEL_LEVEL_INDICES, centre_lat, target_longitudes
        ),
        surface_pressure,
    )
    return relative_humidity_mixed_phase(temperature, humidity)


def build_vertical_sections(
    track: pd.DataFrame,
    paths: SourcePaths,
) -> tuple[dict[str, Any], list[str], str]:
    centres = lifecycle_centres(track)
    shape = (len(PRESSURE_HPA), len(RELATIVE_DEGREES))
    totals = {
        "relative_vorticity": np.zeros(shape, dtype=np.float64),
        "theta_e": np.zeros(shape, dtype=np.float64),
        "relative_humidity": np.zeros(shape, dtype=np.float64),
    }
    counts = {
        "relative_vorticity": np.zeros(shape, dtype=np.int16),
        "theta_e": np.zeros(shape, dtype=np.int16),
        "relative_humidity": np.zeros(shape, dtype=np.int16),
    }
    samples = {"relative_vorticity": 0, "theta_e": 0, "relative_humidity": 0}
    errors: list[str] = []
    source_names: set[str] = set()
    for timestamp, centre_lat, centre_lon in centres:
        try:
            if timestamp.year >= 1979:
                try:
                    vorticity, theta_e, relative_humidity = badc_vertical_snapshot(
                        paths.badc_model, timestamp, centre_lat, centre_lon
                    )
                    source_name = "BADC ERA5 model-level analysis"
                except (FileNotFoundError, OSError, KeyError, IndexError, ValueError):
                    vorticity, theta_e, relative_humidity = arco_vertical_snapshot(
                        timestamp, centre_lat, centre_lon
                    )
                    source_name = "ARCO ERA5 pressure-level analysis (BADC fallback)"
            else:
                vorticity, theta_e, relative_humidity = arco_vertical_snapshot(
                    timestamp, centre_lat, centre_lon
                )
                source_name = "ARCO ERA5 pressure-level analysis"
        except Exception as error:
            errors.append(f"vertical {timestamp:%Y-%m-%d %H:%M}: {error}")
            print(errors[-1], file=sys.stderr, flush=True)
            continue
        source_names.add(source_name)
        for key, field in (
            ("relative_vorticity", vorticity),
            ("theta_e", theta_e),
            ("relative_humidity", relative_humidity),
        ):
            if np.isfinite(field).any():
                add_field(totals[key], counts[key], field)
                samples[key] += 1
    requested = len(centres)
    fields: dict[str, Any] = {}
    source_kind = " + ".join(sorted(source_names)) or "unavailable"
    definitions = {
        "relative_vorticity": (0.01, "10-5 s-1", source_kind),
        "theta_e": (0.1, "K", f"{source_kind}; Bolton (1980) theta-e from T and q"),
        "relative_humidity": (
            0.1,
            "%",
            f"{source_kind}; mixed-phase relative humidity from ERA5 T and q",
        ),
    }
    for key, (scale, units, source) in definitions.items():
        packed = pack_field(
            finite_mean(totals[key], counts[key]),
            scale=scale,
            units=units,
            samples=samples[key],
            requested_samples=requested,
            source=source,
        )
        if packed is not None:
            fields[key] = packed
    return fields, errors, source_kind


def build_relative_humidity_section(
    track: pd.DataFrame,
    paths: SourcePaths,
) -> tuple[dict[str, Any] | None, list[str], str]:
    """Build RH alone so the existing public composite archive can be augmented."""
    centres = lifecycle_centres(track)
    shape = (len(PRESSURE_HPA), len(RELATIVE_DEGREES))
    total = np.zeros(shape, dtype=np.float64)
    count = np.zeros(shape, dtype=np.int16)
    samples = 0
    errors: list[str] = []
    source_names: set[str] = set()
    for timestamp, centre_lat, centre_lon in centres:
        try:
            if timestamp.year >= 1979:
                try:
                    field = badc_relative_humidity_snapshot(
                        paths.badc_model, timestamp, centre_lat, centre_lon
                    )
                    source_name = "BADC ERA5 model-level analysis"
                except (FileNotFoundError, OSError, KeyError, IndexError, ValueError):
                    field = arco_relative_humidity_snapshot(
                        timestamp, centre_lat, centre_lon
                    )
                    source_name = "ARCO ERA5 pressure-level analysis (BADC fallback)"
            else:
                field = arco_relative_humidity_snapshot(timestamp, centre_lat, centre_lon)
                source_name = "ARCO ERA5 pressure-level analysis"
        except Exception as error:
            errors.append(f"relative humidity {timestamp:%Y-%m-%d %H:%M}: {error}")
            print(errors[-1], file=sys.stderr, flush=True)
            continue
        source_names.add(source_name)
        if np.isfinite(field).any():
            add_field(total, count, field)
            samples += 1
    source_kind = " + ".join(sorted(source_names)) or "unavailable"
    packed = pack_field(
        finite_mean(total, count),
        scale=0.1,
        units="%",
        samples=samples,
        requested_samples=len(centres),
        source=f"{source_kind}; mixed-phase relative humidity from ERA5 T and q",
    )
    return packed, errors, source_kind


def composite_asset(
    track_id: int,
    catalogue: Path,
    paths: SourcePaths,
) -> dict[str, Any]:
    track = track_rows(catalogue, track_id)
    precipitation, precip_errors = build_precipitation(track, paths)
    sections, vertical_errors, vertical_source = build_vertical_sections(track, paths)
    return {
        "schema": SCHEMA,
        "release": catalogue_release(catalogue),
        "track_id": int(track_id),
        "track_start": pd.Timestamp(track["time"].iloc[0]).isoformat(),
        "track_end": pd.Timestamp(track["time"].iloc[-1]).isoformat(),
        "built_utc": utc_now(),
        "grid": {
            "frame": "unrotated storm-relative geographic coordinates",
            "relative_longitude_degrees": {
                "start": float(RELATIVE_DEGREES[0]),
                "step": 0.25,
                "count": int(len(RELATIVE_DEGREES)),
            },
            "relative_latitude_degrees": {
                "start": float(RELATIVE_DEGREES[0]),
                "step": 0.25,
                "count": int(len(RELATIVE_DEGREES)),
            },
            "pressure_hpa": PRESSURE_HPA.astype(int).tolist(),
        },
        "method": {
            "precipitation": (
                "Mean UTC-day accumulation across every calendar day touched by the track; "
                "the daily centre is the track position nearest 12 UTC within that day."
            ),
            "vertical": (
                "Zonal section at 0 degrees relative latitude, averaged across nine equally "
                "spaced lifecycle snapshots from genesis through lysis."
            ),
            "vertical_source": vertical_source,
            "theta_e": "Bolton (1980) equivalent potential temperature from ERA5 T and q.",
            "relative_humidity": (
                "ERA5 relative humidity from T and q using mixed-phase saturation vapour "
                "pressure, bounded to 0-100%."
            ),
        },
        "precipitation": precipitation,
        "section": sections,
        "warnings": (precip_errors + vertical_errors)[:80],
    }


def atomic_gzip_json(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    compressed = gzip.compress(encoded, compresslevel=9, mtime=0)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(compressed)
        os.replace(temporary_name, output)
        os.chmod(output, 0o644)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_one(args: argparse.Namespace, track_id: int) -> Path:
    output = args.output / "tracks" / f"track-{track_id}.json.gz"
    if args.relative_humidity_only and output.exists():
        with gzip.open(output, "rt", encoding="utf-8") as stream:
            asset = json.load(stream)
        if asset.get("section", {}).get("relative_humidity") is not None and not args.force:
            print(f"relative humidity exists {output}", flush=True)
            return output
        track = track_rows(args.catalogue, track_id)
        paths = SourcePaths(args.era5_precip, args.imerg_daily, args.badc_model)
        started = time.monotonic()
        field, errors, source_kind = build_relative_humidity_section(track, paths)
        if field is None:
            raise RuntimeError(f"no relative-humidity snapshots available for track {track_id}")
        asset.setdefault("section", {})["relative_humidity"] = field
        asset.setdefault("method", {})["relative_humidity"] = (
            "ERA5 relative humidity from T and q using mixed-phase saturation vapour "
            "pressure, bounded to 0-100%."
        )
        asset["method"]["relative_humidity_source"] = source_kind
        asset["built_utc"] = utc_now()
        asset["warnings"] = (list(asset.get("warnings", [])) + errors)[:80]
        atomic_gzip_json(asset, output)
        print(
            f"augmented relative humidity for track {track_id} in "
            f"{time.monotonic() - started:.1f}s -> {output}",
            flush=True,
        )
        return output
    if output.exists() and not args.force:
        print(f"exists {output}", flush=True)
        return output
    paths = SourcePaths(args.era5_precip, args.imerg_daily, args.badc_model)
    started = time.monotonic()
    asset = composite_asset(track_id, args.catalogue, paths)
    atomic_gzip_json(asset, output)
    print(
        f"built track {track_id} in {time.monotonic() - started:.1f}s: "
        f"precip={','.join(asset['precipitation']) or 'none'} "
        f"section={','.join(asset['section']) or 'none'} -> {output}",
        flush=True,
    )
    return output


def validate_packed_field(
    field: Any,
    expected_shape: tuple[int, int],
    label: str,
) -> list[str]:
    """Return structural and sample-accounting problems in one packed field."""
    problems: list[str] = []
    if not isinstance(field, dict):
        return [f"{label} is not an object"]
    if field.get("shape") != list(expected_shape):
        problems.append(f"{label} shape is {field.get('shape')}, expected {list(expected_shape)}")
    data = field.get("data")
    expected_values = int(np.prod(expected_shape))
    if not isinstance(data, list):
        problems.append(f"{label} data is not an array")
        data = []
    elif len(data) != expected_values:
        problems.append(f"{label} has {len(data)} values, expected {expected_values}")
    invalid_values = sum(
        value is not None and (not isinstance(value, int) or isinstance(value, bool))
        for value in data
    )
    if invalid_values:
        problems.append(f"{label} has {invalid_values} non-integer packed values")
    finite_values = sum(value is not None for value in data)
    if not finite_values:
        problems.append(f"{label} contains no finite values")
    scale = field.get("scale")
    if not isinstance(scale, (int, float)) or scale <= 0:
        problems.append(f"{label} has invalid scale {scale}")
    samples = field.get("samples")
    requested = field.get("requested_samples")
    if not isinstance(samples, int) or samples <= 0:
        problems.append(f"{label} has invalid sample count {samples}")
    if not isinstance(requested, int) or not isinstance(samples, int) or requested < samples:
        problems.append(f"{label} has invalid requested sample count {requested}")
    elif requested > 0:
        expected_availability = round(samples / requested, 4)
        if field.get("availability_fraction") != expected_availability:
            problems.append(
                f"{label} availability is {field.get('availability_fraction')}, "
                f"expected {expected_availability}"
            )
    if data:
        expected_coverage = round(finite_values / len(data), 4)
        if field.get("spatial_coverage_fraction") != expected_coverage:
            problems.append(
                f"{label} spatial coverage is {field.get('spatial_coverage_fraction')}, "
                f"expected {expected_coverage}"
            )
    if not field.get("source") or not field.get("units"):
        problems.append(f"{label} is missing source or units metadata")
    return problems


def catalogue_release(catalogue: Path) -> str:
    match = re.search(r"lps_v(\d+(?:\.\d+)+)", catalogue.name)
    if not match:
        raise ValueError(f"Cannot infer release version from {catalogue.name}")
    return f"LPS v{match.group(1)}"


def build_manifest(output: Path, catalogue: Path) -> Path:
    expected_ids = catalogue_track_ids(catalogue)
    expected_set = {int(value) for value in expected_ids}
    tracks: dict[str, Any] = {}
    corrupt: list[str] = []
    qa_errors: list[str] = []
    extra_track_ids: list[int] = []
    for source in sorted((output / "tracks").glob("track-*.json.gz")):
        try:
            compressed = source.read_bytes()
            asset = json.loads(gzip.decompress(compressed))
            if asset.get("schema") != SCHEMA:
                raise ValueError("schema mismatch")
            numeric_track_id = int(asset["track_id"])
            filename_track_id = int(
                source.name.removeprefix("track-").removesuffix(".json.gz")
            )
            if numeric_track_id != filename_track_id:
                qa_errors.append(
                    f"{source.name}: payload track_id is {numeric_track_id}"
                )
            if numeric_track_id not in expected_set:
                extra_track_ids.append(numeric_track_id)
            track_id = str(numeric_track_id)
            if track_id in tracks:
                qa_errors.append(f"{source.name}: duplicate payload track_id {track_id}")

            precipitation = asset.get("precipitation", {})
            sections = asset.get("section", {})
            if "era5" not in precipitation:
                qa_errors.append(f"track {track_id}: missing ERA5 precipitation")
            for key, field in precipitation.items():
                qa_errors.extend(
                    f"track {track_id}: {problem}"
                    for problem in validate_packed_field(
                        field, (len(RELATIVE_DEGREES), len(RELATIVE_DEGREES)), f"precipitation.{key}"
                    )
                )
            for key in ("relative_vorticity", "theta_e", "relative_humidity"):
                if key not in sections:
                    qa_errors.append(f"track {track_id}: missing section.{key}")
            for key, field in sections.items():
                qa_errors.extend(
                    f"track {track_id}: {problem}"
                    for problem in validate_packed_field(
                        field, (len(PRESSURE_HPA), len(RELATIVE_DEGREES)), f"section.{key}"
                    )
                )
            warnings = asset.get("warnings", [])
            if warnings:
                qa_errors.append(f"track {track_id}: {len(warnings)} source-read warnings")
            tracks[track_id] = {
                "precipitation": sorted(asset.get("precipitation", {})),
                "section": sorted(asset.get("section", {})),
                "samples": {
                    "precipitation": {
                        key: [field.get("samples"), field.get("requested_samples")]
                        for key, field in precipitation.items()
                    },
                    "section": {
                        key: [field.get("samples"), field.get("requested_samples")]
                        for key, field in sections.items()
                    },
                },
                "bytes": len(compressed),
                "sha256": hashlib.sha256(compressed).hexdigest(),
                "warnings": len(warnings),
            }
        except Exception as error:
            corrupt.append(f"{source.name}: {error}")
    missing = [int(value) for value in expected_ids if str(int(value)) not in tracks]
    qa = {
        "errors": qa_errors,
        "tracks_with_era5_precipitation": sum(
            "era5" in value["precipitation"] for value in tracks.values()
        ),
        "tracks_with_imerg_precipitation": sum(
            "imerg" in value["precipitation"] for value in tracks.values()
        ),
        "tracks_with_all_sections": sum(
            value["section"]
            == ["relative_humidity", "relative_vorticity", "theta_e"]
            for value in tracks.values()
        ),
        "tracks_with_source_read_warnings": sum(
            value["warnings"] > 0 for value in tracks.values()
        ),
    }
    manifest = {
        "schema": "monsoon-low-atlas-storm-composite-manifest-v1",
        "release": catalogue_release(catalogue),
        "built_utc": utc_now(),
        "expected_tracks": int(len(expected_ids)),
        "completed_tracks": int(len(tracks)),
        "complete": not missing and not corrupt and not extra_track_ids and not qa_errors,
        "missing_track_ids": missing,
        "extra_track_ids": sorted(set(extra_track_ids)),
        "corrupt": corrupt,
        "qa": qa,
        "tracks": tracks,
        "method": {
            "precipitation": "lifecycle mean UTC-day accumulation on a +/-10 degree, 0.25-degree storm-relative grid",
            "vertical": "nine-snapshot lifecycle-mean zonal section at zero relative latitude, 1000-100 hPa",
        },
    }
    destination = output / "manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".manifest.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        os.replace(temporary_name, destination)
        os.chmod(destination, 0o644)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    print(
        f"manifest: {len(tracks)}/{len(expected_ids)} tracks, "
        f"missing={len(missing)}, corrupt={len(corrupt)}, "
        f"qa_errors={len(qa_errors)} -> {destination}",
        flush=True,
    )
    return destination


def parser() -> argparse.ArgumentParser:
    project = Path(__file__).resolve().parents[2]
    release = project / "lps-v5.3-continuity-framework" / "production" / "v5.6"
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--catalogue",
        type=Path,
        default=release / "public-release" / "lps_v5.6-era5-1940-2025-core.parquet",
    )
    result.add_argument("--output", type=Path, required=True)
    selection = result.add_mutually_exclusive_group()
    selection.add_argument("--track-id", type=int)
    selection.add_argument("--array-index", type=int)
    selection.add_argument("--manifest", action="store_true")
    result.add_argument(
        "--era5-precip",
        type=Path,
        default=Path("/home/users/kieran/ncas/data/era5-incompass/hourly_precip_SA"),
    )
    result.add_argument(
        "--imerg-daily",
        type=Path,
        default=Path("/home/users/kieran/ncas/data/IMERG_daily"),
    )
    result.add_argument(
        "--badc-model",
        type=Path,
        default=Path("/badc/ecmwf-era5/data/oper/an_ml"),
    )
    result.add_argument("--force", action="store_true")
    result.add_argument(
        "--relative-humidity-only",
        action="store_true",
        help="augment an existing per-track asset with RH without rereading other fields",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    if args.manifest:
        build_manifest(args.output, args.catalogue)
        return
    track_id = args.track_id
    if args.array_index is not None:
        ids = catalogue_track_ids(args.catalogue)
        if args.array_index < 0 or args.array_index >= len(ids):
            raise IndexError(
                f"array index {args.array_index} outside 0..{len(ids) - 1}"
            )
        track_id = int(ids[args.array_index])
    if track_id is None:
        raise SystemExit("choose --track-id, --array-index or --manifest")
    build_one(args, int(track_id))


if __name__ == "__main__":
    main()
