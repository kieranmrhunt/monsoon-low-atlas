#!/usr/bin/env python3
"""Standardise the local BADC ERA-Interim archive for LPS tracking."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
import xarray as xr

from .common import PRESSURE_LEVELS, TARGET_LATS, TARGET_LONS, atomic_to_netcdf, require_variables, sha256, target_grid
from .standardise_merra2 import month_bounds, standard_paths, validate_month


DEFAULT_ARCHIVE = Path("/badc/ecmwf-era-interim/data")
STANDARD_SCHEMA = "lps-atlas-reanalysis-standard-month-v1"
FINAL_ANALYSIS = pd.Timestamp("2019-08-31T18:00:00")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def analysis_path(root: Path, family: str, stamp: pd.Timestamp) -> Path:
    prefix = "ggap" if family == "pressure" else "ggas"
    branch = "gg/ap" if family == "pressure" else "gg/as"
    return root / branch / f"{stamp:%Y}" / f"{stamp:%m}" / f"{stamp:%d}" / f"{prefix}{stamp:%Y%m%d%H}00.nc"


def forecast_path(root: Path, initialization: pd.Timestamp, lead_hours: int) -> Path:
    return (
        root
        / "ga/fs"
        / f"{initialization:%Y}"
        / f"{initialization:%m}"
        / f"{initialization:%d}"
        / f"gafs{initialization:%Y%m%d%H}{lead_hours:02d}.nc"
    )


def regional(value: xr.Dataset) -> xr.Dataset:
    latitude = value["latitude"]
    if float(latitude[0]) > float(latitude[-1]):
        value = value.sel(latitude=slice(50.0, -20.0))
    else:
        value = value.sel(latitude=slice(-20.0, 50.0))
    longitude = value["longitude"]
    if float(longitude.min()) < 0 and float(longitude.max()) <= 180:
        value = value.sel(longitude=slice(40.0, 125.0))
    else:
        value = value.sel(longitude=slice(40.0, 125.0))
    return value


def _single_analysis(path: Path, variables: Sequence[str], stamp: pd.Timestamp, *, pressure: bool) -> xr.Dataset:
    if not path.is_file():
        raise FileNotFoundError(path)
    with xr.open_dataset(path, decode_times=False) as source:
        require_variables(source, variables, path)
        value = source[list(variables)]
        if "t" in value.dims:
            value = value.isel(t=0, drop=True)
        if pressure:
            levels = np.asarray(value["p"].values, dtype=float)
            indexes = [int(np.nanargmin(np.abs(levels - wanted))) for wanted in PRESSURE_LEVELS]
            if any(abs(levels[index] - wanted) > 0.5 for index, wanted in zip(indexes, PRESSURE_LEVELS, strict=True)):
                raise ValueError(f"{path} lacks one of {PRESSURE_LEVELS.tolist()} hPa")
            value = value.isel(p=indexes).rename({"p": "level"})
        value = value.squeeze(drop=True)
        value = target_grid(regional(value)).astype(np.float32).load()
    return value.expand_dims(time=[stamp])


def open_analyses(
    root: Path,
    family: str,
    stamps: Iterable[pd.Timestamp],
    variables: Sequence[str],
) -> xr.Dataset:
    pressure = family == "pressure"
    parts = [
        _single_analysis(analysis_path(root, family, stamp), variables, stamp, pressure=pressure)
        for stamp in stamps
    ]
    return xr.concat(parts, dim="time", data_vars="minimal", coords="minimal", compat="override").sortby("time")


def interpolation_source_times(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    wanted = pd.date_range(start, end, freq="6h")
    available = wanted[wanted <= FINAL_ANALYSIS]
    if available.empty:
        raise ValueError(f"ERA-Interim has no analyses for {start:%Y-%m}")
    return available


def interpolate_time(value: xr.Dataset, times: pd.DatetimeIndex) -> xr.Dataset:
    coordinate = xr.DataArray(times, dims="time", coords={"time": times})
    return value.interp(time=coordinate, method="linear", kwargs={"fill_value": "extrapolate"})


def _single_forecast(path: Path, initialization: pd.Timestamp, lead_hours: int) -> xr.DataArray:
    if not path.is_file():
        raise FileNotFoundError(path)
    with xr.open_dataset(path, decode_times=False) as source:
        require_variables(source, ("TP",), path)
        value = source[["TP"]]
        if "t" in value.dims:
            value = value.isel(t=0, drop=True)
        if "surface" in value.dims:
            value = value.isel(surface=0, drop=True)
        sampled = target_grid(regional(value)).astype(np.float32).load()["TP"]
    return sampled


def precipitation(root: Path, start: pd.Timestamp, end: pd.Timestamp) -> xr.DataArray:
    initializations = pd.date_range(start - pd.Timedelta(hours=12), end, freq="12h")
    hourly: list[xr.DataArray] = []
    for initialization in initializations:
        previous: xr.DataArray | None = None
        for lead in (3, 6, 9, 12):
            path = forecast_path(root, initialization, lead)
            if not path.is_file():
                previous = None
                continue
            accumulated = _single_forecast(path, initialization, lead)
            if previous is None and lead != 3:
                previous = accumulated
                continue
            interval = accumulated if previous is None else accumulated - previous
            previous = accumulated
            interval = xr.where(interval >= -1.0e-8, interval, 0.0)
            end_time = initialization + pd.Timedelta(hours=lead)
            rate = interval * np.float32(1000.0 / 3.0)
            for offset in (2, 1, 0):
                hourly.append(rate.expand_dims(time=[end_time - pd.Timedelta(hours=offset)]))
    if not hourly:
        raise RuntimeError(f"No ERA-Interim precipitation forecasts cover {start:%Y-%m}")
    combined = xr.concat(hourly, dim="time").sortby("time")
    times = pd.DatetimeIndex(combined.time.values)
    _, indexes = np.unique(times.view("int64"), return_index=True)
    combined = combined.isel(time=np.sort(indexes))
    expected = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    combined = combined.reindex(time=expected)
    combined = combined.interpolate_na(dim="time", method="nearest", fill_value="extrapolate")
    combined.attrs.update({"long_name": "total precipitation rate", "units": "mm h-1"})
    return combined


def dataset_attrs(month: str, temporal_basis: str) -> dict[str, str]:
    return {
        "title": f"ERA-Interim fields standardized for LPS atlas comparison, {month}",
        "source": "ECMWF ERA-Interim via the CEDA/BADC archive",
        "coverage": "1979-01-01 through 2019-08-31",
        "grid": "1 degree, 45--120E, 15S--45N",
        "temporal_basis": temporal_basis,
        "processing": "regional hyperslab; bilinear spatial interpolation; linear temporal interpolation",
        "created_utc": utc_now(),
    }


def standardise_month(root: Path, archive: Path, month: str) -> dict[str, object]:
    start, end = month_bounds(month)
    source_times = interpolation_source_times(start, end)
    pressure = open_analyses(archive, "pressure", source_times, ("U", "V", "T", "R", "VO"))
    surface = open_analyses(archive, "surface", source_times, ("MSL", "SP", "U10", "V10"))
    hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    native_three_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="3h")
    pressure_hourly = interpolate_time(pressure, hourly)
    surface_hourly = interpolate_time(surface, hourly)
    pressure_three_hourly = interpolate_time(pressure, native_three_hourly)

    common = {
        "time": hourly,
        "level": PRESSURE_LEVELS,
        "latitude": TARGET_LATS,
        "longitude": TARGET_LONS,
    }
    vorticity = xr.Dataset(
        {"vo": (("time", "level", "latitude", "longitude"), np.asarray(pressure_hourly["VO"].values, dtype=np.float32) * np.float32(1.0e5))},
        coords=common,
        attrs=dataset_attrs(month, "hourly interpolation of six-hourly analyses"),
    )
    vorticity["vo"].attrs.update({"long_name": "relative vorticity", "units": "10^-5 s^-1"})
    surface_output = xr.Dataset(
        {
            "msl": surface_hourly["MSL"],
            "sp": surface_hourly["SP"],
            "u10": surface_hourly["U10"],
            "v10": surface_hourly["V10"],
        },
        attrs=dataset_attrs(month, "hourly interpolation of six-hourly analyses"),
    ).astype(np.float32)
    rain = precipitation(archive, start, end)
    precipitation_output = xr.Dataset(
        {"mtpr": rain / np.float32(3600.0)},
        attrs=dataset_attrs(month, "three-hour forecast accumulations spread uniformly to hourly rates"),
    ).astype(np.float32)
    precipitation_output["mtpr"].attrs.update({"long_name": "total precipitation rate", "units": "kg m-2 s-1"})
    auxiliary = xr.Dataset(
        {
            "u": pressure_three_hourly["U"],
            "v": pressure_three_hourly["V"],
            "t": pressure_three_hourly["T"],
            "r": pressure_three_hourly["R"].clip(0.0, 100.0),
        },
        attrs=dataset_attrs(month, "three-hour interpolation of six-hourly analyses"),
    ).astype(np.float32)
    for name in ("u", "v"):
        auxiliary[name].attrs["units"] = "m s-1"
    auxiliary["t"].attrs["units"] = "K"
    auxiliary["r"].attrs["units"] = "%"

    paths = standard_paths(root, month)
    for name, dataset in (
        ("vorticity", vorticity),
        ("surface", surface_output),
        ("precipitation", precipitation_output),
        ("auxiliary", auxiliary),
    ):
        atomic_to_netcdf(dataset, paths[name])
    next_month = end.strftime("%Y%m")
    next_auxiliary = standard_paths(root, next_month)["auxiliary"]
    if end > FINAL_ANALYSIS and not next_auxiliary.exists():
        boundary = interpolate_time(pressure, pd.DatetimeIndex([end]))
        boundary_dataset = xr.Dataset(
            {
                "u": boundary["U"],
                "v": boundary["V"],
                "t": boundary["T"],
                "r": boundary["R"].clip(0.0, 100.0),
            },
            attrs={**dataset_attrs(next_month, "boundary-only interpolation of six-hourly analyses"), "coverage": "boundary-only"},
        ).astype(np.float32)
        for name in ("u", "v"):
            boundary_dataset[name].attrs["units"] = "m s-1"
        boundary_dataset["t"].attrs["units"] = "K"
        boundary_dataset["r"].attrs["units"] = "%"
        atomic_to_netcdf(boundary_dataset, next_auxiliary)
    report: dict[str, object] = {
        "schema": STANDARD_SCHEMA,
        "source": "ERA-Interim",
        "month": month,
        "created_utc": utc_now(),
        "coverage": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "files": {},
    }
    for name in ("vorticity", "surface", "precipitation", "auxiliary"):
        path = paths[name]
        report["files"][name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    paths["provenance"].parent.mkdir(parents=True, exist_ok=True)
    temporary = paths["provenance"].with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, paths["provenance"])
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/reanalyses/erainterim"))
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    standardise = subparsers.add_parser("standardise-month")
    standardise.add_argument("--month", required=True)
    validate = subparsers.add_parser("validate-month")
    validate.add_argument("--month", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = standardise_month(args.root, args.archive, args.month) if args.command == "standardise-month" else validate_month(args.root, args.month)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
