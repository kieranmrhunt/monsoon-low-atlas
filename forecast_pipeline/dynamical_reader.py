"""Isolated Icechunk reader; emits regional arrays, never forecast tracks.

Run with the Python >=3.12 environment in requirements-dynamical.txt. The
operational Python environment consumes the NPZ using allow_pickle=False and
continues to run the unchanged frozen detector and linker.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

DATASETS = {
    "aifs": "ecmwf-aifs-single-forecast-virtual",
    "gefs-extended": "noaa-gefs-forecast-35-day-0-5-degree-virtual",
}
LEVELS = (850, 700, 500)
# Required rainfall was added later than the nominal AIFS archive start.
# https://dynamical.org/catalog/ecmwf-aifs-single-forecast-virtual/validation/
TRACKABLE_START = {"aifs": "2025022406", "gefs-extended": "2020100100"}


def transient_read(call, attempts=6):
    """Retry the same field after provider throttling, never fill missing data."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception as error:
            message = str(error).lower()
            transient = any(token in message for token in (
                "slowdown", "too many requests", "toomanyrequests", "timed out",
                "timeout", "connection reset", "temporarily unavailable",
                "service unavailable", "status code: 503", "status code: 429",
            ))
            if not transient or attempt + 1 == attempts:
                raise
            delay = min(45, 2 ** (attempt + 1)) + random.uniform(0, 1)
            logging.getLogger(__name__).warning("Transient provider read; retry %d/%d in %.1f s", attempt + 2, attempts, delay)
            time.sleep(delay)


def open_groups(model: str):
    import dynamical_catalog

    # Resolve the current storage/version through STAC, never a pinned bucket.
    dataset = DATASETS[model]
    return (
        dynamical_catalog.open(dataset, chunks=None),
        dynamical_catalog.open(dataset, group="pressure_level", chunks=None),
    )


def unit_factor(units: str, kind: str) -> float:
    units = units.lower().replace(" ", "").replace("**", "^")
    accepted = {
        "wind": {"ms-1": 1, "ms^-1": 1, "m/s": 1},
        "pressure": {"pa": .01, "hpa": 1, "mbar": 1},
        "rain": {"kgm-2": 1, "kgm^-2": 1, "mm": 1, "m": 1000},
    }
    if units not in accepted[kind]:
        raise ValueError(f"Unsupported {kind} units: {units!r}")
    return accepted[kind][units]


def cumulative_rain(values: np.ndarray, steps: list[int], model: str) -> np.ndarray:
    if steps != list(range(0, steps[-1] + 1, 6)):
        raise ValueError("Rainfall requires a complete six-hourly axis beginning at zero")
    values = np.asarray(values, dtype=np.float32).copy()
    values[0] = 0  # no accumulation exists at initialization
    if not np.isfinite(values).all() or np.min(values) < -.01:
        raise ValueError("Missing or negative precipitation in a required interval")
    values = np.maximum(values, 0)
    if model == "gefs-extended":
        return np.cumsum(values, axis=0, dtype=np.float32)
    if np.min(np.diff(values, axis=0)) < -.05:
        raise ValueError("AIFS run-total rainfall resets within the requested cycle")
    return np.maximum.accumulate(values, axis=0)


def regional_field(variable, selection: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    selected = variable.sel(selection).sel(latitude=lats, longitude=lons, method="nearest")
    for name, expected in (("latitude", lats), ("longitude", lons)):
        if not np.allclose(selected[name].values, expected, atol=1e-5, rtol=0):
            raise ValueError(f"{name} does not align with the atlas grid")
    return transient_read(lambda: np.asarray(selected.transpose("latitude", "longitude").values, dtype=np.float32))


def read_member(model: str, cycle: str, member: str, steps: list[int],
                lats: np.ndarray, lons: np.ndarray, workers: int = 4,
                groups=None) -> tuple[dict, dict]:
    if model not in DATASETS:
        raise ValueError(model)
    if cycle < TRACKABLE_START[model]:
        raise ValueError(f"{model} lacks the complete frozen-tracker inputs before {TRACKABLE_START[model]}")
    if model == "aifs" and member != "det":
        raise ValueError("AIFS Single has only the deterministic member")
    if model == "gefs-extended" and not (
        member == "c00" or member.startswith("p") and member[1:].isdigit() and 1 <= int(member[1:]) <= 30
    ):
        raise ValueError(f"Invalid GEFS member {member}")
    if steps != list(range(0, steps[-1] + 1, 6)):
        raise ValueError("Require complete six-hourly lead times beginning at zero")
    surface, pressure = groups if groups is not None else open_groups(model)
    stamp = np.datetime64(datetime.strptime(cycle, "%Y%m%d%H"), "ns")
    selection = {"init_time": stamp}
    if model == "gefs-extended":
        if not cycle.endswith("00"):
            raise ValueError("Extended GEFS is initialized at 00 UTC only")
        selection["ensemble_member"] = 0 if member == "c00" else int(member[1:])
    # Exact initialization, pressure levels and leads: never select a nearby run.
    surface = surface.sel(selection)
    pressure = pressure.sel(selection).sel(pressure_level=list(LEVELS))
    rain_name = "total_precipitation_surface" if model == "gefs-extended" else "total_precipitation_run_total_surface"
    specs = {
        "mslp": (surface["pressure_reduced_to_mean_sea_level"], "pressure"),
        "u10": (surface["wind_u_10m"], "wind"),
        "v10": (surface["wind_v_10m"], "wind"),
        "rain": (surface[rain_name], "rain"),
    }
    for level in LEVELS:
        for component in ("u", "v"):
            specs[f"{component}{level}"] = (
                pressure[f"wind_{component}"].sel(pressure_level=level), "wind"
            )
    factors = {key: unit_factor(field.attrs.get("units", ""), kind)
               for key, (field, kind) in specs.items()}

    def read_step(step):
        frame = {}
        for key, (field, kind) in specs.items():
            if key == "rain" and step == 0:
                values = np.zeros((len(lats), len(lons)), dtype=np.float32)
            else:
                values = regional_field(field, {"lead_time": np.timedelta64(step, "h")}, lats, lons) * factors[key]
            finite = np.isfinite(values)
            # Match the ordinary GRIB decoder's complete-grid contract. Do not
            # invent winds if a different source generation masks terrain.
            if not finite.all():
                raise ValueError(f"{model} {cycle} {member} +{step}: {key} has {finite.mean():.1%} finite cells")
            if kind == "wind" and np.nanmax(np.abs(values)) > 200:
                raise ValueError(f"Implausible {key} wind")
            if kind == "pressure" and not (800 < np.nanmin(values) <= np.nanmax(values) < 1100):
                raise ValueError("Implausible mean-sea-level pressure")
            frame[key] = values
        return frame

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(steps)))) as pool:
        frames = list(pool.map(read_step, steps))
    arrays = {key: np.stack([frame[key] for frame in frames]) for key in specs}
    arrays["rain"] = cumulative_rain(arrays["rain"], steps, model)
    metadata = {
        "schema": "mla-dynamical-member-v1", "dataset": DATASETS[model],
        "model": model, "cycle": cycle, "member": member, "steps": steps,
        "rainfall": "six-hour interval totals accumulated" if model == "gefs-extended" else "run-total accumulation",
        "units": {"mslp": "hPa", "rain": "mm", "wind": "m s-1"},
        "finite_fraction": {key: float(np.isfinite(value).mean()) for key, value in arrays.items()},
    }
    return arrays, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(DATASETS), required=True)
    parser.add_argument("--cycle")
    parser.add_argument("--member", default="det")
    parser.add_argument("--horizon", type=int, default=360)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    groups = open_groups(args.model)
    if args.inventory:
        surface, pressure = groups
        print(json.dumps({
            "dataset": DATASETS[args.model],
            "cycles": [str(value) for value in surface.init_time.values],
            "steps": (surface.lead_time.values / np.timedelta64(1, "h")).astype(int).tolist(),
            "members": surface.ensemble_member.values.tolist() if "ensemble_member" in surface.coords else ["det"],
            "pressure_levels": pressure.pressure_level.values.tolist(),
        }))
        return
    if not args.cycle or not args.output or args.horizon < 6 or args.horizon % 6:
        parser.error("reading requires cycle, output and a positive six-hourly horizon")
    steps = list(range(0, args.horizon + 1, 6))
    lats, lons = np.arange(-15, 46, dtype=np.float32), np.arange(45, 121, dtype=np.float32)
    arrays, metadata = read_member(args.model, args.cycle, args.member, steps, lats, lons, args.workers, groups)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f".part-{os.getpid()}")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, **arrays, latitude=lats, longitude=lons,
                            metadata=np.asarray(json.dumps(metadata, allow_nan=False)))
    os.replace(temporary, args.output)
    print(json.dumps(metadata, allow_nan=False))


if __name__ == "__main__":
    main()
