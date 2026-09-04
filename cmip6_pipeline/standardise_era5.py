#!/usr/bin/env python3
"""Sample ERA5 to the common CMIP6 detector grid without retuning.

This is the resolution-control experiment for the climate-change tab.  ERA5
pressure-level winds are sampled at the exact 1-degree target nodes before
vorticity is recomputed, so the detector sees the same spatial grid and file
contract as every CMIP6 model.  Surface pressure is estimated reproducibly from
hourly MSLP and fixed ERA5 orography because the CEDA logarithmic-pressure tree
is incomplete for the control period; an actual-lnsp option remains for audits.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from reanalysis_pipeline.common import (
    PRESSURE_LEVELS,
    TARGET_LATS,
    TARGET_LONS,
    atomic_to_netcdf,
    relative_vorticity_x1e5,
    sha256,
)
from reanalysis_pipeline.standardise_merra2 import month_bounds, standard_paths

from .standardise import validate_month


SCHEMA = "lps-atlas-era5-common-grid-month-v1"
DEFAULT_SOURCE_ROOT = Path("/home/users/kieran/ncas/data/era5-incompass")
DEFAULT_BADC_ROOT = Path("/badc/ecmwf-era5/data")
STANDARD_GRAVITY_MS2 = 9.80665
STANDARD_LAPSE_PRESSURE_COEFFICIENT_M1 = 2.25577e-5
STANDARD_LAPSE_PRESSURE_EXPONENT = 5.25588


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _normalise(dataset: xr.Dataset) -> xr.Dataset:
    rename = {}
    for source, target in (
        ("valid_time", "time"),
        ("pressure_level", "level"),
        ("lat", "latitude"),
        ("lon", "longitude"),
    ):
        if source in dataset.dims or source in dataset.coords:
            if target not in dataset.dims and target not in dataset.coords:
                rename[source] = target
    return dataset.rename(rename)


def _sample_exact_nodes(value: xr.Dataset | xr.DataArray) -> xr.Dataset | xr.DataArray:
    """Select common nodes, falling back to linear sampling when grids differ."""

    value = _normalise(value) if isinstance(value, xr.Dataset) else _normalise(value.to_dataset(name=value.name or "value"))[value.name or "value"]
    latitude = np.asarray(value.latitude.values, dtype=float)
    longitude = np.asarray(value.longitude.values, dtype=float)
    lat_matches = all(np.any(np.isclose(latitude, target, atol=1.0e-6)) for target in TARGET_LATS)
    lon_matches = all(np.any(np.isclose(longitude, target, atol=1.0e-6)) for target in TARGET_LONS)
    if lat_matches and lon_matches:
        sampled = value.sel(
            latitude=xr.DataArray(TARGET_LATS, dims="latitude"),
            longitude=xr.DataArray(TARGET_LONS, dims="longitude"),
            method="nearest",
            tolerance=1.0e-5,
        )
        return sampled.assign_coords(latitude=TARGET_LATS, longitude=TARGET_LONS)
    if latitude[0] > latitude[-1]:
        value = value.sortby("latitude")
    if longitude[0] > longitude[-1]:
        value = value.sortby("longitude")
    return value.interp(
        latitude=xr.DataArray(TARGET_LATS, dims="latitude", coords={"latitude": TARGET_LATS}),
        longitude=xr.DataArray(TARGET_LONS, dims="longitude", coords={"longitude": TARGET_LONS}),
        method="linear",
    )


def _monthly_path(source_root: Path, collection: str, month: str) -> Path:
    path = source_root / collection / f"{month}.nc"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _open_pressure(source_root: Path, month: str) -> tuple[xr.Dataset, list[Path]]:
    start, end = month_bounds(month)
    next_month = end.strftime("%Y%m")
    paths = [
        _monthly_path(source_root, "3hourly_pl_SA", month),
        _monthly_path(source_root, "3hourly_pl_SA", next_month),
    ]
    parts: list[xr.Dataset] = []
    for path in paths:
        with xr.open_dataset(path) as source:
            value = _normalise(source[["u", "v", "t", "q"]])
            value = value.sel(time=slice(start, end), level=PRESSURE_LEVELS)
            if value.sizes.get("time", 0):
                parts.append(_sample_exact_nodes(value).astype(np.float32).load())
    combined = xr.concat(parts, dim="time", data_vars="minimal", coords="minimal", compat="override")
    combined = combined.sortby("time")
    times = pd.DatetimeIndex(pd.to_datetime(combined.time.values))
    _, keep = np.unique(times.view("int64"), return_index=True)
    combined = combined.isel(time=np.sort(keep))
    expected = pd.date_range(start, end, freq="3h")
    actual = pd.DatetimeIndex(pd.to_datetime(combined.time.values))
    if not actual.equals(expected):
        raise ValueError(f"ERA5 pressure time axis is {actual.min()}..{actual.max()}, expected {expected.min()}..{expected.max()}")
    return combined, paths


def _open_monthly_surface(source_root: Path, month: str) -> tuple[xr.Dataset, Path]:
    path = _monthly_path(source_root, "hourly_sfc_SA", month)
    with xr.open_dataset(path) as source:
        value = _sample_exact_nodes(_normalise(source[["msl", "u10", "v10"]])).astype(np.float32).load()
    return value, path


def _open_monthly_precipitation(source_root: Path, month: str) -> tuple[xr.DataArray, Path]:
    path = _monthly_path(source_root, "hourly_precip_SA", month)
    with xr.open_dataset(path) as source:
        value = _sample_exact_nodes(_normalise(source[["mtpr"]])).astype(np.float32).load()["mtpr"]
    return value, path


def lnsp_path(badc_root: Path, timestamp: pd.Timestamp) -> Path:
    stamp = timestamp.strftime("%Y%m%d%H00")
    return (
        badc_root
        / "oper"
        / "an_ml"
        / timestamp.strftime("%Y")
        / timestamp.strftime("%m")
        / timestamp.strftime("%d")
        / f"ecmwf-era5_oper_an_ml_{stamp}.lnsp.nc"
    )


def surface_pressure_from_badc(badc_root: Path, times: pd.DatetimeIndex) -> tuple[xr.DataArray, list[Path]]:
    values = np.empty((len(times), len(TARGET_LATS), len(TARGET_LONS)), dtype=np.float32)
    paths: list[Path] = []
    for index, timestamp in enumerate(times):
        path = lnsp_path(badc_root, timestamp)
        if not path.is_file():
            raise FileNotFoundError(path)
        with xr.open_dataset(path) as source:
            dataset = _normalise(source[["lnsp"]])
            sampled = _sample_exact_nodes(dataset["lnsp"]).isel(time=0).load()
            values[index] = np.exp(np.asarray(sampled.values, dtype=np.float64)).astype(np.float32)
        paths.append(path)
    result = xr.DataArray(
        values,
        dims=("time", "latitude", "longitude"),
        coords={"time": times, "latitude": TARGET_LATS, "longitude": TARGET_LONS},
        name="sp",
    )
    result.attrs.update({"long_name": "surface pressure", "units": "Pa"})
    return result, paths


def estimated_surface_pressure(msl: xr.DataArray, static_file: Path) -> xr.DataArray:
    with xr.open_dataset(static_file) as source:
        if "z" not in source:
            raise ValueError(f"{static_file} does not contain surface geopotential z")
        static = _sample_exact_nodes(_normalise(source[["z"]])).load()
    units = str(static.z.attrs.get("units", "")).lower().replace(" ", "")
    if "m**2" not in units and "m2s-2" not in units:
        raise ValueError(f"{static_file}: cannot interpret z units {static.z.attrs.get('units')!r}")
    orography = np.asarray(static.z.values, dtype=np.float32) / np.float32(STANDARD_GRAVITY_MS2)
    pressure_hpa = np.asarray(msl.values, dtype=np.float32) / np.float32(100.0)
    base = np.float32(1.0) - np.float32(STANDARD_LAPSE_PRESSURE_COEFFICIENT_M1) * orography
    if np.any(base <= 0.0):
        raise ValueError("static orography is outside the standard-atmosphere pressure range")
    estimate_pa = pressure_hpa * np.power(base[None, :, :], np.float32(STANDARD_LAPSE_PRESSURE_EXPONENT)) * np.float32(100.0)
    result = xr.DataArray(
        estimate_pa.astype(np.float32),
        dims=("time", "latitude", "longitude"),
        coords={"time": msl.time, "latitude": TARGET_LATS, "longitude": TARGET_LONS},
        name="sp",
    )
    result.attrs.update(
        {
            "long_name": "estimated surface pressure for pressure-level validity",
            "units": "Pa",
            "source": "standard-atmosphere reduction of ERA5 MSLP using fixed ERA5 orography",
        }
    )
    return result


def _attrs(month: str, temporal_basis: str) -> dict[str, str]:
    return {
        "title": f"ERA5 sampled to the common CMIP6 LPS grid, {month}",
        "source": "ECMWF ERA5 local regional extracts and fixed ERA5 orography",
        "grid": "1 degree, 45--120E, 15S--45N",
        "temporal_basis": temporal_basis,
        "processing": "exact-node 1-degree spatial sampling; vorticity recomputed from sampled winds; frozen v5.6 detector input contract",
        "experiment_role": "ERA5-as-model spatial-resolution control",
        "created_utc": utc_now(),
    }


def _require_axis(value: xr.Dataset | xr.DataArray, expected: pd.DatetimeIndex, label: str) -> None:
    actual = pd.DatetimeIndex(pd.to_datetime(value.time.values))
    if not actual.equals(expected):
        raise ValueError(f"{label} time axis is {actual.min()}..{actual.max()}, expected {expected.min()}..{expected.max()}")


def standardise_month(
    output_root: Path,
    source_root: Path,
    badc_root: Path,
    month: str,
    *,
    static_file: Path,
    surface_pressure_source: str = "estimate",
) -> dict[str, Any]:
    paths = standard_paths(output_root, month)
    provenance = paths["provenance"]
    if provenance.is_file():
        try:
            cached = json.loads(provenance.read_text(encoding="utf-8"))
            if cached.get("surface_pressure_source") != surface_pressure_source:
                raise ValueError("cached standard month uses a different surface-pressure source")
            if surface_pressure_source == "estimate" and cached.get("static_sha256") != sha256(static_file):
                raise ValueError("cached standard month uses a different static field")
            validate_month(output_root, month)
            return cached
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass

    start, end = month_bounds(month)
    hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    three_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="3h")
    pressure, pressure_paths = _open_pressure(source_root, month)
    surface, surface_path = _open_monthly_surface(source_root, month)
    precipitation, precipitation_path = _open_monthly_precipitation(source_root, month)
    if surface_pressure_source == "actual-lnsp":
        surface_pressure_values, surface_pressure_paths = surface_pressure_from_badc(badc_root, hourly)
        surface_pressure_record: dict[str, Any] = {
            "method": "actual ERA5 exp(lnsp)",
            "archive": str(badc_root),
            "variable": "lnsp",
            "files": len(surface_pressure_paths),
            "first": str(surface_pressure_paths[0]),
            "last": str(surface_pressure_paths[-1]),
        }
    elif surface_pressure_source == "estimate":
        surface_pressure_values = estimated_surface_pressure(surface.msl, static_file)
        surface_pressure_record = {
            "method": "standard-atmosphere estimate from hourly ERA5 MSLP and fixed orography",
            "static_file": str(static_file),
            "static_sha256": sha256(static_file),
            "audit": "data/cmip6-inventory/era5-surface-pressure-estimate-canary.json",
        }
    else:
        raise ValueError(f"unsupported surface-pressure source {surface_pressure_source!r}")
    for value, expected, label in (
        (surface, hourly, "surface"),
        (precipitation, hourly, "precipitation"),
    ):
        _require_axis(value, expected, label)

    hourly_coordinate = xr.DataArray(hourly, dims="time", coords={"time": hourly})
    winds_hourly = pressure[["u", "v"]].interp(time=hourly_coordinate, method="linear")
    vorticity = relative_vorticity_x1e5(
        np.asarray(winds_hourly.u.transpose("time", "level", "latitude", "longitude").values, dtype=np.float32),
        np.asarray(winds_hourly.v.transpose("time", "level", "latitude", "longitude").values, dtype=np.float32),
        latitudes=TARGET_LATS,
        longitudes=TARGET_LONS,
    )
    coordinates = {
        "time": hourly,
        "level": PRESSURE_LEVELS,
        "latitude": TARGET_LATS,
        "longitude": TARGET_LONS,
    }
    vorticity_output = xr.Dataset(
        {"vo": (("time", "level", "latitude", "longitude"), vorticity)},
        coords=coordinates,
        attrs=_attrs(month, "hourly interpolation of three-hourly instantaneous pressure-level winds"),
    )
    vorticity_output.vo.attrs.update({"long_name": "relative vorticity", "units": "10^-5 s^-1"})

    surface_output = xr.Dataset(
        {
            "msl": surface.msl.transpose("time", "latitude", "longitude"),
            "sp": surface_pressure_values,
            "u10": surface.u10.transpose("time", "latitude", "longitude"),
            "v10": surface.v10.transpose("time", "latitude", "longitude"),
        },
        attrs=_attrs(month, "native hourly instantaneous fields"),
    ).astype(np.float32)
    for name, long_name, units in (
        ("msl", "mean sea level pressure", "Pa"),
        ("sp", "surface pressure", "Pa"),
        ("u10", "10 m eastward wind", "m s-1"),
        ("v10", "10 m northward wind", "m s-1"),
    ):
        surface_output[name].attrs.update({"long_name": long_name, "units": units})

    precipitation_output = xr.Dataset(
        {"mtpr": precipitation.transpose("time", "latitude", "longitude")},
        attrs=_attrs(month, "native hourly mean precipitation rate"),
    ).astype(np.float32)
    precipitation_output.mtpr.attrs.update({"long_name": "total precipitation rate", "units": "kg m-2 s-1"})

    auxiliary_current = pressure.sel(time=three_hourly)
    auxiliary_output = xr.Dataset(
        {
            name: auxiliary_current[name].transpose("time", "level", "latitude", "longitude")
            for name in ("u", "v", "t", "q")
        },
        attrs=_attrs(month, "native three-hourly instantaneous pressure-level fields"),
    ).astype(np.float32)
    for name in ("u", "v"):
        auxiliary_output[name].attrs["units"] = "m s-1"
    auxiliary_output.t.attrs["units"] = "K"
    auxiliary_output.q.attrs["units"] = "kg kg-1"

    for name, dataset in (
        ("vorticity", vorticity_output),
        ("surface", surface_output),
        ("precipitation", precipitation_output),
        ("auxiliary", auxiliary_output),
    ):
        atomic_to_netcdf(dataset, paths[name])

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "created_utc": utc_now(),
        "month": month,
        "experiment_role": "ERA5-as-model spatial-resolution control",
        "surface_pressure_source": surface_pressure_source,
        "static_sha256": sha256(static_file) if surface_pressure_source == "estimate" else None,
        "source_files": {
            "pressure_level": [
                {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in pressure_paths
            ],
            "surface": {"path": str(surface_path), "bytes": surface_path.stat().st_size, "sha256": sha256(surface_path)},
            "precipitation": {"path": str(precipitation_path), "bytes": precipitation_path.stat().st_size, "sha256": sha256(precipitation_path)},
            "surface_pressure": surface_pressure_record,
        },
        "outputs": {},
        "method": (
            "ERA5 values at the common 1-degree nodes; three-hourly pressure-level winds "
            "interpolated to hourly before spherical vorticity is recomputed; native hourly "
            "surface and precipitation fields; surface pressure from the explicitly recorded "
            f"{surface_pressure_source} method."
        ),
    }
    for name in ("vorticity", "surface", "precipitation", "auxiliary"):
        path = paths[name]
        report["outputs"][name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    _atomic_json(provenance, report)
    validate_month(output_root, month)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--badc-root", type=Path, default=DEFAULT_BADC_ROOT)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--surface-pressure-source", choices=("estimate", "actual-lnsp"), default="estimate")
    parser.add_argument("--month", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = standardise_month(
        args.output_root,
        args.source_root,
        args.badc_root,
        args.month,
        static_file=args.static_file,
        surface_pressure_source=args.surface_pressure_source,
    )
    print(json.dumps({"month": report["month"], "schema": report["schema"], "status": "complete"}))


if __name__ == "__main__":
    main()
