#!/usr/bin/env python3
"""Download regional JRA-55 subsets and standardise them for LPS tracking."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import xarray as xr

from .common import PRESSURE_LEVELS, TARGET_LATS, TARGET_LONS, atomic_to_netcdf, require_variables, sha256, target_grid
from .standardise_merra2 import month_bounds, standard_paths, validate_month


NCSS_ROOT = "https://tds.gdex.ucar.edu/thredds/ncss/grid/aggregations/g/d628000"
PRESSURE_DATASET = f"{NCSS_ROOT}/8/TP"
SURFACE_DATASET = f"{NCSS_ROOT}/13/TP"
PRECIPITATION_DATASET = f"{NCSS_ROOT}/22/TP"
FINAL_ANALYSIS = pd.Timestamp("2024-01-31T18:00:00")
FINAL_PRECIPITATION_BOUND = pd.Timestamp("2024-02-01T00:00:00")
STANDARD_SCHEMA = "lps-atlas-reanalysis-standard-month-v1"

PRESSURE_VARIABLES = (
    "Relative_vorticity_isobaric_surface_low",
    "u-component_of_wind_isobaric_surface_low",
    "v-component_of_wind_isobaric_surface_low",
    "Temperature_isobaric_surface_low",
)
HUMIDITY_VARIABLE = "Relative_humidity_isobaric_surface_low"
SURFACE_VARIABLES = (
    "Pressure_surface",
    "Pressure_reduced_to_MSL_msl",
    "u-component_of_wind_height_above_ground",
    "v-component_of_wind_height_above_ground",
)
PRECIPITATION_VARIABLE = "Total_precipitation_surface_Average"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def raw_paths(root: Path, month: str) -> dict[str, Path]:
    directory = root / "raw" / "jra55" / month
    result = {
        f"pressure_{int(level)}": directory / f"pressure-{int(level)}.nc"
        for level in PRESSURE_LEVELS
    }
    result.update(
        {
            f"humidity_{int(level)}": directory / f"humidity-{int(level)}.nc"
            for level in PRESSURE_LEVELS
        }
    )
    result["surface"] = directory / "surface.nc"
    result["precipitation"] = directory / "precipitation.nc"
    return result


def iso(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def ncss_url(
    dataset: str,
    variables: Sequence[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    level: float | None = None,
) -> str:
    parameters: list[tuple[str, str]] = [("var", variable) for variable in variables]
    parameters.extend(
        [
            ("north", "45"),
            ("west", "40"),
            ("east", "125"),
            ("south", "-20"),
            ("horizStride", "1"),
            ("time_start", iso(start)),
            ("time_end", iso(end)),
            ("timeStride", "1"),
            ("accept", "netcdf3"),
        ]
    )
    if level is not None:
        parameters.append(("vertCoord", f"{level:g}"))
    return f"{dataset}?{urlencode(parameters)}"


def validate_raw_file(
    path: Path,
    variables: Sequence[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    level: float | None = None,
) -> None:
    if not path.is_file() or path.stat().st_size <= 1024:
        raise ValueError(f"JRA-55 response was unexpectedly small: {path}")
    with xr.open_dataset(path) as dataset:
        require_variables(dataset, variables, path)
        if "time" not in dataset.coords or not dataset.sizes.get("time"):
            raise ValueError(f"JRA-55 response has no time coordinate: {path}")
        times = pd.DatetimeIndex(pd.to_datetime(dataset["time"].values))
        if times.min() < start - pd.Timedelta(minutes=1) or times.min() > start + pd.Timedelta(hours=3):
            raise ValueError(f"JRA-55 response starts at {times.min()}, not {start}: {path}")
        if times.max() < end - pd.Timedelta(hours=6) or times.max() > end + pd.Timedelta(hours=3):
            raise ValueError(f"JRA-55 response ends at {times.max()}, not near {end}: {path}")
        if level is not None:
            coordinate = next(
                (dataset[name] for name in ("isobaric_surface_low", "isobaric_surface_low1") if name in dataset.coords),
                None,
            )
            values = np.asarray(coordinate.values, dtype=float).reshape(-1) if coordinate is not None else np.asarray([])
            if values.size != 1 or abs(float(values[0]) - level) > 0.5:
                raise ValueError(f"JRA-55 response is not the requested {level:g}-hPa surface: {path}")


def download_file(
    url: str,
    path: Path,
    *,
    variables: Sequence[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    level: float | None = None,
    attempts: int = 8,
) -> None:
    if path.is_file():
        try:
            validate_raw_file(path, variables, start, end, level=level)
            return
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    request = Request(url, headers={"User-Agent": "monsoon-low-atlas/1.0"})
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=300) as response, temporary.open("wb") as stream:
                while chunk := response.read(8 * 1024 * 1024):
                    stream.write(chunk)
            validate_raw_file(temporary, variables, start, end, level=level)
            os.replace(temporary, path)
            return
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            temporary.unlink(missing_ok=True)
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(60, 2 ** (attempt + 1)))


def download_month(root: Path, month: str) -> dict[str, object]:
    start, end = month_bounds(month)
    if start < pd.Timestamp("1958-01-01") or start > pd.Timestamp("2024-01-01"):
        raise ValueError("JRA-55 d628000 monthly coverage is 1958-01 through 2024-01")
    paths = raw_paths(root, month)
    analysis_end = min(end, FINAL_ANALYSIS)
    for level in PRESSURE_LEVELS:
        download_file(
            ncss_url(PRESSURE_DATASET, PRESSURE_VARIABLES, start, analysis_end, level=float(level)),
            paths[f"pressure_{int(level)}"],
            variables=PRESSURE_VARIABLES,
            start=start,
            end=analysis_end,
            level=float(level),
        )
        download_file(
            ncss_url(PRESSURE_DATASET, (HUMIDITY_VARIABLE,), start, analysis_end, level=float(level)),
            paths[f"humidity_{int(level)}"],
            variables=(HUMIDITY_VARIABLE,),
            start=start,
            end=analysis_end,
            level=float(level),
        )
    download_file(
        ncss_url(SURFACE_DATASET, SURFACE_VARIABLES, start, analysis_end),
        paths["surface"],
        variables=SURFACE_VARIABLES,
        start=start,
        end=analysis_end,
    )
    precipitation_end = min(end, FINAL_PRECIPITATION_BOUND) - pd.Timedelta(hours=3)
    download_file(
        ncss_url(PRECIPITATION_DATASET, (PRECIPITATION_VARIABLE,), start, precipitation_end),
        paths["precipitation"],
        variables=(PRECIPITATION_VARIABLE,),
        start=start,
        end=precipitation_end,
    )
    return {
        "source": "JRA-55",
        "month": month,
        "status": "downloaded",
        "files": {
            name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for name, path in paths.items()
        },
    }


def _drop_vertical(value: xr.DataArray) -> xr.DataArray:
    for dimension in ("isobaric_surface_low", "isobaric_surface_low1", "height_above_ground", "height_above_ground1"):
        if dimension in value.dims:
            if value.sizes[dimension] != 1:
                raise ValueError(f"expected one JRA-55 {dimension} value")
            value = value.isel({dimension: 0}, drop=True)
    return value


def _sample_field(dataset: xr.Dataset, variable: str) -> xr.DataArray:
    require_variables(dataset, (variable,), Path("JRA-55 source dataset"))
    value = _drop_vertical(dataset[variable])
    # NCAR's historical aggregations include scalar metadata coordinates such as
    # ``reftime`` whose values can differ between fields in the same month. They
    # are not physical dimensions and would otherwise make xarray reject a
    # perfectly valid field merge.
    value = value.reset_coords(drop=True)
    return target_grid(value).astype(np.float32).load()


def open_pressure(root: Path, month: str) -> xr.Dataset:
    paths = raw_paths(root, month)
    levels: list[xr.Dataset] = []
    for level in PRESSURE_LEVELS:
        pressure_path = paths[f"pressure_{int(level)}"]
        humidity_path = paths[f"humidity_{int(level)}"]
        with xr.open_dataset(pressure_path) as source, xr.open_dataset(humidity_path) as humidity:
            current = xr.Dataset(
                {
                    "vo": _sample_field(source, PRESSURE_VARIABLES[0]),
                    "u": _sample_field(source, PRESSURE_VARIABLES[1]),
                    "v": _sample_field(source, PRESSURE_VARIABLES[2]),
                    "t": _sample_field(source, PRESSURE_VARIABLES[3]),
                    "r": _sample_field(humidity, HUMIDITY_VARIABLE),
                }
            ).expand_dims(level=[level])
            levels.append(current)
    return xr.concat(levels, dim="level", data_vars="all", coords="minimal", compat="override").sortby("time")


def open_surface(root: Path, month: str) -> xr.Dataset:
    path = raw_paths(root, month)["surface"]
    with xr.open_dataset(path) as source:
        result = xr.Dataset(
            {
                "sp": _sample_field(source, SURFACE_VARIABLES[0]),
                "msl": _sample_field(source, SURFACE_VARIABLES[1]),
                "u10": _sample_field(source, SURFACE_VARIABLES[2]),
                "v10": _sample_field(source, SURFACE_VARIABLES[3]),
            }
        ).load()
    return result.sortby("time")


def precipitation_hourly(root: Path, month: str) -> xr.DataArray:
    start, end = month_bounds(month)
    path = raw_paths(root, month)["precipitation"]
    with xr.open_dataset(path) as source:
        require_variables(source, (PRECIPITATION_VARIABLE, "time_bounds"), path)
        rain = target_grid(source[PRECIPITATION_VARIABLE]).astype(np.float32).load()
        bounds = np.asarray(source["time_bounds"].values).astype("datetime64[ns]")
    hourly: list[xr.DataArray] = []
    for index, (left_value, right_value) in enumerate(bounds):
        left = pd.Timestamp(left_value)
        right = pd.Timestamp(right_value)
        if left < start or right > end or right - left != pd.Timedelta(hours=3):
            continue
        rate = rain.isel(time=index, drop=True) / np.float32(86400.0)
        for offset in range(3):
            hourly.append(rate.expand_dims(time=[left + pd.Timedelta(hours=offset)]))
    if not hourly:
        raise ValueError(f"no JRA-55 precipitation intervals cover {month}")
    result = xr.concat(hourly, dim="time").sortby("time")
    expected = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    actual = pd.DatetimeIndex(pd.to_datetime(result.time.values))
    if not actual.equals(expected):
        raise ValueError(f"JRA-55 precipitation time axis is incomplete for {month}")
    result.attrs.update({"long_name": "total precipitation rate", "units": "kg m-2 s-1"})
    return result


def _interpolate(value: xr.Dataset, times: pd.DatetimeIndex) -> xr.Dataset:
    coordinate = xr.DataArray(times, dims="time", coords={"time": times})
    return value.interp(time=coordinate, method="linear", kwargs={"fill_value": "extrapolate"})


def _canonical_pressure_dimensions(value: xr.Dataset) -> xr.Dataset:
    """Keep pressure-level fields in the tracker-wide canonical dimension order."""
    return value.transpose("time", "level", "latitude", "longitude")


def dataset_attrs(month: str, temporal_basis: str) -> dict[str, str]:
    return {
        "title": f"JRA-55 fields standardized for LPS atlas comparison, {month}",
        "source": "JMA JRA-55 via NSF NCAR GDEX dataset d628000",
        "source_doi": "10.5065/D6HH6H41",
        "coverage": "1958-01-01 through 2024-01-31",
        "grid": "1 degree, 45--120E, 15S--45N",
        "temporal_basis": temporal_basis,
        "processing": "regional NCSS subset; bilinear spatial interpolation; linear temporal interpolation",
        "created_utc": utc_now(),
    }


def standardise_month(root: Path, month: str) -> dict[str, object]:
    start, end = month_bounds(month)
    pressure = open_pressure(root, month)
    surface = open_surface(root, month)
    hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    three_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="3h")
    pressure_hourly = _interpolate(pressure, hourly)
    surface_hourly = _interpolate(surface, hourly)
    pressure_three_hourly = _interpolate(pressure, three_hourly)

    vorticity = xr.Dataset(
        {"vo": pressure_hourly["vo"].transpose("time", "level", "latitude", "longitude") * np.float32(1.0e5)},
        attrs=dataset_attrs(month, "hourly interpolation of six-hourly analyses"),
    ).astype(np.float32)
    vorticity["vo"].attrs.update({"long_name": "relative vorticity", "units": "10^-5 s^-1"})
    surface_output = surface_hourly[["msl", "sp", "u10", "v10"]].astype(np.float32)
    surface_output.attrs = dataset_attrs(month, "hourly interpolation of six-hourly analyses")
    surface_output["msl"].attrs.update({"long_name": "mean sea level pressure", "units": "Pa"})
    surface_output["sp"].attrs.update({"long_name": "surface pressure", "units": "Pa"})
    surface_output["u10"].attrs.update({"long_name": "10 m eastward wind", "units": "m s-1"})
    surface_output["v10"].attrs.update({"long_name": "10 m northward wind", "units": "m s-1"})
    precipitation_output = xr.Dataset(
        {"mtpr": precipitation_hourly(root, month)},
        attrs=dataset_attrs(month, "three-hour precipitation rates distributed over their bounded hours"),
    ).astype(np.float32)
    auxiliary = _canonical_pressure_dimensions(
        pressure_three_hourly[["u", "v", "t", "r"]]
    ).astype(np.float32)
    auxiliary["r"] = auxiliary["r"].clip(0.0, 100.0)
    auxiliary.attrs = dataset_attrs(month, "three-hour interpolation of six-hourly analyses")
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

    if end > FINAL_ANALYSIS:
        next_month = end.strftime("%Y%m")
        next_auxiliary = standard_paths(root, next_month)["auxiliary"]
        boundary = _canonical_pressure_dimensions(
            _interpolate(pressure, pd.DatetimeIndex([end]))[["u", "v", "t", "r"]]
        ).astype(np.float32)
        boundary["r"] = boundary["r"].clip(0.0, 100.0)
        boundary.attrs = {**dataset_attrs(next_month, "extrapolated final boundary record"), "coverage": "boundary-only"}
        atomic_to_netcdf(boundary, next_auxiliary)

    report: dict[str, object] = {
        "schema": STANDARD_SCHEMA,
        "source": "JRA-55",
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
    parser.add_argument("--root", type=Path, default=Path("data/reanalyses/jra55"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("download-month", "standardise-month", "validate-month", "process-month"):
        child = subparsers.add_parser(command)
        child.add_argument("--month", required=True, help="YYYYMM")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "download-month":
        result = download_month(args.root, args.month)
    elif args.command == "standardise-month":
        result = standardise_month(args.root, args.month)
    elif args.command == "validate-month":
        result = validate_month(args.root, args.month)
    else:
        download_month(args.root, args.month)
        standardise_month(args.root, args.month)
        result = validate_month(args.root, args.month)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
