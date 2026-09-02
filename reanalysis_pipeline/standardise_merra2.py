#!/usr/bin/env python3
"""Convert native MERRA-2 subsets to the frozen LPS detector contract."""

from __future__ import annotations

import argparse
import calendar
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
import xarray as xr

from .common import (
    PRESSURE_LEVELS,
    TARGET_LATS,
    TARGET_LONS,
    atomic_to_netcdf,
    relative_vorticity_x1e5,
    require_variables,
    sha256,
    target_grid,
)
from .merra2 import output_path as raw_output_path


DEFAULT_PRECIP_ROOT = Path("/home/users/kieran/ncas/data/MERRA-2")
STANDARD_SCHEMA = "lps-atlas-reanalysis-standard-month-v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_bounds(value: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(datetime.strptime(value, "%Y%m"))
    days = calendar.monthrange(start.year, start.month)[1]
    return start, start + pd.Timedelta(days=days)


def daily_dates(start: pd.Timestamp, end_inclusive: pd.Timestamp) -> list[datetime.date]:
    return [item.date() for item in pd.date_range(start.normalize(), end_inclusive.normalize(), freq="D")]


def _normalise_and_load(dataset: xr.Dataset, variables: Sequence[str]) -> xr.Dataset:
    require_variables(dataset, variables, Path("MERRA-2 source dataset"))
    selected = dataset[list(variables)]
    if "lev" in selected.coords:
        available = np.asarray(selected["lev"].values, dtype=float)
        indexes = [int(np.nanargmin(np.abs(available - level))) for level in PRESSURE_LEVELS]
        if any(abs(available[index] - level) > 0.5 for index, level in zip(indexes, PRESSURE_LEVELS, strict=True)):
            raise ValueError(f"MERRA-2 pressure file lacks one of {PRESSURE_LEVELS.tolist()} hPa")
        selected = selected.isel(lev=indexes)
    sampled = target_grid(selected)
    return sampled.astype(np.float32).load()


def open_native_days(paths: Iterable[Path], variables: Sequence[str]) -> xr.Dataset:
    parts: list[xr.Dataset] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        with xr.open_dataset(path) as source:
            parts.append(_normalise_and_load(source, variables))
    if not parts:
        raise ValueError("no MERRA-2 files were supplied")
    combined = xr.concat(parts, dim="time", data_vars="minimal", coords="minimal", compat="override")
    times = pd.DatetimeIndex(pd.to_datetime(combined["time"].values))
    _, indexes = np.unique(times.view("int64"), return_index=True)
    return combined.isel(time=np.sort(indexes)).sortby("time")


def resolve_precipitation_file(
    root: Path,
    day: datetime.date,
    *,
    raw_root: Path | None = None,
) -> Path:
    stamp = day.strftime("%Y%m%d")
    candidates = [
        *sorted((root / "precip").glob(f"*{stamp}*.nc*")),
        *sorted(
            (root / "merra2_flx_precip_subdaily_daily" / f"{day:%Y}" / f"{day:%m}").glob(
                f"*{stamp}*.nc*"
            )
        ),
    ]
    if raw_root is not None:
        candidates.insert(0, raw_output_path(raw_root, "precipitation", day))
    if not candidates:
        raise FileNotFoundError(f"No local MERRA-2 PRECTOT file found for {day} below {root}")
    return candidates[0]


def label_precipitation_at_interval_end(values: xr.DataArray) -> xr.DataArray:
    """Shift M2T1NXFLX midpoint timestamps to their hourly interval end."""

    return values.assign_coords(
        time=pd.DatetimeIndex(pd.to_datetime(values.time.values)) + pd.Timedelta(minutes=30)
    )


def precipitation_for_month(
    root: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    raw_root: Path | None = None,
) -> xr.DataArray:
    """Return hourly PRECTOT on the common grid, labelled at interval end."""

    days = daily_dates(start - pd.Timedelta(days=1), end - pd.Timedelta(days=1))
    parts: list[xr.DataArray] = []
    for day in days:
        try:
            path = resolve_precipitation_file(root, day, raw_root=raw_root)
        except FileNotFoundError:
            # MERRA-2 begins at 00 UTC 1 January 1980, so the trailing interval
            # ending at that exact first hour has no preceding source record.
            if start == pd.Timestamp("1980-01-01") and day == datetime(1979, 12, 31).date():
                continue
            raise
        with xr.open_dataset(path) as source:
            require_variables(source, ("PRECTOT",), path)
            sampled = target_grid(source[["PRECTOT"]]).astype(np.float32).load()["PRECTOT"]
            # M2T1NXFLX timestamps are interval midpoints (00:30 is 00--01).
            # The detector uses trailing hourly rain, so label each mean at the
            # end of its accumulation interval.
            sampled = label_precipitation_at_interval_end(sampled)
            parts.append(sampled)
    combined = xr.concat(parts, dim="time").sortby("time")
    times = pd.DatetimeIndex(pd.to_datetime(combined.time.values))
    _, indexes = np.unique(times.view("int64"), return_index=True)
    combined = combined.isel(time=np.sort(indexes)).sel(time=slice(start, end - pd.Timedelta(hours=1)))
    expected = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    actual = pd.DatetimeIndex(pd.to_datetime(combined.time.values))
    missing = expected.difference(actual)
    allowed_missing = pd.DatetimeIndex([pd.Timestamp("1980-01-01T00:00")]) if start == pd.Timestamp("1980-01-01") else pd.DatetimeIndex([])
    if len(missing.difference(allowed_missing)) or len(actual.difference(expected)):
        raise ValueError(f"MERRA-2 precipitation axis is {actual.min()}..{actual.max()}, expected {expected.min()}..{expected.max()}")
    return combined.reindex(time=expected)


def _require_time_axis(dataset: xr.Dataset, expected: pd.DatetimeIndex, label: str) -> None:
    actual = pd.DatetimeIndex(pd.to_datetime(dataset.time.values))
    if not actual.equals(expected):
        raise ValueError(
            f"{label} time axis has {len(actual)} values from {actual.min()} to {actual.max()}; "
            f"expected {len(expected)} from {expected.min()} to {expected.max()}"
        )


def _dataset_attrs(month: str, temporal_basis: str) -> dict[str, str]:
    return {
        "title": f"MERRA-2 fields standardized for LPS atlas comparison, {month}",
        "source": "NASA GMAO MERRA-2 M2I3NPASM, M2I1NXASM and M2T1NXFLX",
        "source_doi_pressure": "10.5067/QBZ6MG944HW0",
        "source_doi_surface": "10.5067/3Z173KIE2TPD",
        "grid": "1 degree, 45--120E, 15S--45N",
        "temporal_basis": temporal_basis,
        "processing": "bilinear spatial sampling; linear temporal interpolation for instantaneous fields",
        "created_utc": utc_now(),
    }


def standard_paths(root: Path, month: str) -> dict[str, Path]:
    return {
        "vorticity": root / "standard" / "vorticity" / f"{month}.nc",
        "surface": root / "standard" / "surface" / f"{month}.nc",
        "precipitation": root / "standard" / "precipitation" / f"{month}.nc",
        "auxiliary": root / "standard" / "auxiliary" / f"pl3h-{month}.nc",
        "provenance": root / "standard" / "provenance" / f"{month}.json",
    }


def standardise_month(root: Path, precip_root: Path, month: str) -> dict[str, object]:
    start, end = month_bounds(month)
    raw_days = daily_dates(start, end)
    pressure_paths = [raw_output_path(root, "pressure", day) for day in raw_days]
    surface_paths = [raw_output_path(root, "surface", day) for day in raw_days]
    pressure = open_native_days(pressure_paths, ("U", "V", "T", "RH")).sel(time=slice(start, end))
    surface = open_native_days(surface_paths, ("U10M", "V10M", "SLP", "PS")).sel(time=slice(start, end))
    expected_native = pd.date_range(start, end, freq="3h")
    _require_time_axis(pressure, expected_native, "pressure")
    _require_time_axis(surface, expected_native, "surface")

    hourly_times = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    hourly_coord = xr.DataArray(hourly_times, dims="time", coords={"time": hourly_times})
    pressure_hourly = pressure[["U", "V"]].interp(time=hourly_coord, method="linear")
    surface_hourly = surface.interp(time=hourly_coord, method="linear")
    eastward = np.asarray(
        pressure_hourly["U"].transpose("time", "level", "latitude", "longitude").values,
        dtype=np.float32,
    )
    northward = np.asarray(
        pressure_hourly["V"].transpose("time", "level", "latitude", "longitude").values,
        dtype=np.float32,
    )
    vorticity = relative_vorticity_x1e5(
        eastward,
        northward,
        latitudes=TARGET_LATS,
        longitudes=TARGET_LONS,
    )
    common_coords = {
        "time": hourly_times,
        "level": PRESSURE_LEVELS,
        "latitude": TARGET_LATS,
        "longitude": TARGET_LONS,
    }
    vorticity_dataset = xr.Dataset(
        {"vo": (("time", "level", "latitude", "longitude"), vorticity)},
        coords=common_coords,
        attrs=_dataset_attrs(month, "hourly instantaneous fields"),
    )
    vorticity_dataset["vo"].attrs.update({"long_name": "relative vorticity", "units": "10^-5 s^-1"})

    surface_dataset = xr.Dataset(
        {
            "msl": surface_hourly["SLP"].transpose("time", "latitude", "longitude"),
            "sp": surface_hourly["PS"].transpose("time", "latitude", "longitude"),
            "u10": surface_hourly["U10M"].transpose("time", "latitude", "longitude"),
            "v10": surface_hourly["V10M"].transpose("time", "latitude", "longitude"),
        },
        attrs=_dataset_attrs(month, "hourly instantaneous fields"),
    ).astype(np.float32)
    surface_dataset["msl"].attrs.update({"long_name": "mean sea level pressure", "units": "Pa"})
    surface_dataset["sp"].attrs.update({"long_name": "surface pressure", "units": "Pa"})
    surface_dataset["u10"].attrs.update({"long_name": "10 m eastward wind", "units": "m s-1"})
    surface_dataset["v10"].attrs.update({"long_name": "10 m northward wind", "units": "m s-1"})

    precipitation = precipitation_for_month(precip_root, start, end, raw_root=root)
    precipitation_dataset = xr.Dataset(
        {"mtpr": precipitation.transpose("time", "latitude", "longitude")},
        attrs=_dataset_attrs(month, "hourly mean precipitation labelled at interval end"),
    ).astype(np.float32)
    precipitation_dataset["mtpr"].attrs.update(
        {"long_name": "total precipitation rate", "units": "kg m-2 s-1"}
    )

    native_current = pressure.sel(time=slice(start, end - pd.Timedelta(hours=1)))
    relative_humidity = np.clip(native_current["RH"] * np.float32(100.0), 0.0, 100.0)
    auxiliary_dataset = xr.Dataset(
        {
            "u": native_current["U"].transpose("time", "level", "latitude", "longitude"),
            "v": native_current["V"].transpose("time", "level", "latitude", "longitude"),
            "t": native_current["T"].transpose("time", "level", "latitude", "longitude"),
            "r": relative_humidity.transpose("time", "level", "latitude", "longitude"),
        },
        attrs=_dataset_attrs(month, "native three-hourly instantaneous fields"),
    ).astype(np.float32)
    for name in ("u", "v"):
        auxiliary_dataset[name].attrs["units"] = "m s-1"
    auxiliary_dataset["t"].attrs["units"] = "K"
    auxiliary_dataset["r"].attrs["units"] = "%"

    paths = standard_paths(root, month)
    atomic_to_netcdf(vorticity_dataset, paths["vorticity"])
    atomic_to_netcdf(surface_dataset, paths["surface"])
    atomic_to_netcdf(precipitation_dataset, paths["precipitation"])
    atomic_to_netcdf(auxiliary_dataset, paths["auxiliary"])

    # The detector requires next-month 00 UTC to interpolate the final two
    # hours. Seed that one boundary record now; a later full month atomically
    # replaces it.
    next_month = end.strftime("%Y%m")
    next_auxiliary_path = standard_paths(root, next_month)["auxiliary"]
    next_month_has_full_input = raw_output_path(root, "pressure", (end + pd.Timedelta(days=1)).date()).exists()
    if not next_month_has_full_input:
        boundary = pressure.sel(time=[end]).copy()
        boundary_rh = np.clip(boundary["RH"] * np.float32(100.0), 0.0, 100.0)
        boundary_dataset = xr.Dataset(
            {
                "u": boundary["U"],
                "v": boundary["V"],
                "t": boundary["T"],
                "r": boundary_rh,
            },
            attrs={**_dataset_attrs(next_month, "boundary-only three-hourly record"), "coverage": "boundary-only"},
        ).astype(np.float32)
        for name in ("u", "v"):
            boundary_dataset[name].attrs["units"] = "m s-1"
        boundary_dataset["t"].attrs["units"] = "K"
        boundary_dataset["r"].attrs["units"] = "%"
        atomic_to_netcdf(boundary_dataset, next_auxiliary_path)

    report: dict[str, object] = {
        "schema": STANDARD_SCHEMA,
        "source": "MERRA-2",
        "month": month,
        "created_utc": utc_now(),
        "coverage": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "grid": {"longitude": [45.0, 120.0, 1.0], "latitude": [-15.0, 45.0, 1.0]},
        "files": {},
    }
    for name in ("vorticity", "surface", "precipitation", "auxiliary"):
        path = paths[name]
        report["files"][name] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    paths["provenance"].parent.mkdir(parents=True, exist_ok=True)
    temporary = paths["provenance"].with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, paths["provenance"])
    return report


def validate_month(root: Path, month: str) -> dict[str, object]:
    start, end = month_bounds(month)
    expected_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    expected_native = pd.date_range(start, end - pd.Timedelta(hours=1), freq="3h")
    paths = standard_paths(root, month)
    contracts = {
        "vorticity": (("vo",), expected_hourly, ("time", "level", "latitude", "longitude")),
        "surface": (("msl", "sp", "u10", "v10"), expected_hourly, ("time", "latitude", "longitude")),
        "precipitation": (("mtpr",), expected_hourly, ("time", "latitude", "longitude")),
        "auxiliary": (("u", "v", "t", "r"), expected_native, ("time", "level", "latitude", "longitude")),
    }
    result: dict[str, object] = {"month": month, "status": "passed", "files": {}}
    for name, (variables, expected, dimensions) in contracts.items():
        path = paths[name]
        with xr.open_dataset(path) as dataset:
            require_variables(dataset, variables, path)
            _require_time_axis(dataset, expected, name)
            if dataset.sizes.get("latitude") != len(TARGET_LATS) or dataset.sizes.get("longitude") != len(TARGET_LONS):
                raise ValueError(f"{path} does not use the common 1-degree grid")
            for variable in variables:
                if dataset[variable].dims != dimensions:
                    raise ValueError(f"{path}:{variable} dimensions are {dataset[variable].dims}, expected {dimensions}")
            if "level" in dimensions:
                levels = np.asarray(dataset["level"].values, dtype=float)
                if levels.shape != PRESSURE_LEVELS.shape or not np.allclose(levels, PRESSURE_LEVELS):
                    raise ValueError(f"{path} does not use the common pressure levels")
            finite = {
                variable: round(float(np.isfinite(dataset[variable].values).mean()), 6)
                for variable in variables
            }
        result["files"][name] = {"path": str(path), "finite_fraction": finite}
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/reanalyses/merra2"))
    parser.add_argument("--precip-root", type=Path, default=DEFAULT_PRECIP_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    standardise = subparsers.add_parser("standardise-month")
    standardise.add_argument("--month", required=True, help="YYYYMM")
    validate = subparsers.add_parser("validate-month")
    validate.add_argument("--month", required=True, help="YYYYMM")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "standardise-month":
        report = standardise_month(args.root, args.precip_root, args.month)
    else:
        report = validate_month(args.root, args.month)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
