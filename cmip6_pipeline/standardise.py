#!/usr/bin/env python3
"""Standardise a BADC CMIP6 interval for the frozen v5.6 LPS detector.

Source-native fields are regionally sampled to the tracker's 1-degree grid.
For non-Gregorian models, the interval name and time coordinate refer to an
invertible ordinal analysis clock; native calendar identity is restored after
tracking. Instantaneous fields are linearly interpolated in time;
precipitation fluxes retain their native interval means at each hourly bin
rather than being smoothed across interval boundaries.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import xarray as xr

from reanalysis_pipeline.common import (
    PRESSURE_LEVELS,
    TARGET_LATS,
    TARGET_LONS,
    atomic_to_netcdf,
    relative_vorticity_x1e5,
    require_variables,
    sha256,
    target_grid,
)
from reanalysis_pipeline.standardise_merra2 import month_bounds, standard_paths

from .model_calendar import TimeAxis, load_time_axis, native_stamp
from .source import DEFAULT_ROOT, RunSpec, files_overlapping, files_overlapping_stamps


STANDARD_SCHEMA = "lps-atlas-cmip6-standard-month-v1"
FIELD_TABLES = {
    "ua": "6hrPlevPt",
    "va": "6hrPlevPt",
    "ta": "6hrPlevPt",
    "hus": "6hrPlevPt",
    "psl": "6hrPlevPt",
    "ps": "3hr",
    "uas": "3hr",
    "vas": "3hr",
    "pr": "3hr",
}
FIELD_TABLE_OVERRIDES = {
    # SSP2-4.5 has no 6hrPlevPt psl for this model. Use the same available
    # six-hour pressure table for its historical and future halves.
    ("HadGEM3-GC31-LL", "psl"): "6hrPlev",
}


def field_table(spec: RunSpec, variable: str) -> str:
    return FIELD_TABLE_OVERRIDES.get((spec.source_id, variable), FIELD_TABLES[variable])


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalise_coordinates(value: xr.DataArray) -> xr.DataArray:
    rename = {}
    for source, target in (("lat", "latitude"), ("lon", "longitude"), ("plev", "level")):
        if source in value.dims or source in value.coords:
            rename[source] = target
    value = value.rename(rename)
    if "latitude" not in value.coords or "longitude" not in value.coords:
        raise ValueError(f"{value.name} does not have rectilinear latitude/longitude coordinates")
    if value.latitude.ndim != 1 or value.longitude.ndim != 1:
        raise ValueError(f"{value.name} is not on a rectilinear grid")
    if float(value.latitude[0]) > float(value.latitude[-1]):
        value = value.sortby("latitude")
    if float(value.longitude[0]) > float(value.longitude[-1]):
        value = value.sortby("longitude")
    return value


def _regional(value: xr.DataArray, margin: float = 2.0) -> xr.DataArray:
    value = _normalise_coordinates(value)
    longitudes = np.asarray(value.longitude.values, dtype=float)
    if np.nanmin(longitudes) < 0.0:
        value = value.assign_coords(longitude=np.mod(value.longitude, 360.0)).sortby("longitude")
    return value.sel(
        latitude=slice(float(TARGET_LATS[0]) - margin, float(TARGET_LATS[-1]) + margin),
        longitude=slice(float(TARGET_LONS[0]) - margin, float(TARGET_LONS[-1]) + margin),
    )


def _select_levels(value: xr.DataArray) -> xr.DataArray:
    if "level" not in value.coords:
        raise ValueError(f"{value.name} lacks a pressure coordinate")
    levels = np.asarray(value.level.values, dtype=float)
    units = str(value.level.attrs.get("units", "")).lower()
    if "pa" in units and "hpa" not in units or np.nanmedian(np.abs(levels)) > 2_000:
        levels = levels / 100.0
    indexes = [int(np.nanargmin(np.abs(levels - wanted))) for wanted in PRESSURE_LEVELS]
    if any(abs(levels[index] - wanted) > 0.5 for index, wanted in zip(indexes, PRESSURE_LEVELS, strict=True)):
        raise ValueError(f"{value.name} lacks one of {PRESSURE_LEVELS.tolist()} hPa")
    selected = value.isel(level=indexes).assign_coords(level=PRESSURE_LEVELS)
    selected.level.attrs = {"long_name": "pressure", "units": "hPa", "positive": "down"}
    return selected


def _open_variable(
    root: Path,
    spec: RunSpec,
    variable: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    pressure_levels: bool = False,
    time_axis: TimeAxis | None = None,
) -> tuple[xr.DataArray, list[Path]]:
    table = field_table(spec, variable)
    directory = spec.field_directory(root, table, variable)
    read_start = start - pd.Timedelta(hours=6)
    read_end = end + pd.Timedelta(hours=6)
    if time_axis is None:
        paths = files_overlapping(directory, read_start, read_end)
    else:
        native_start, native_end = time_axis.native_bounds_for_analysis_interval(
            read_start,
            read_end,
        )
        paths = files_overlapping_stamps(
            directory,
            native_stamp(native_start),
            native_stamp(native_end),
        )
    parts: list[xr.DataArray] = []
    for path in paths:
        with xr.open_dataset(path) as dataset:
            if variable not in dataset:
                raise ValueError(f"{path} does not contain {variable}")
            calendar = str(dataset.time.encoding.get("calendar", dataset.time.attrs.get("calendar", "standard")))
            if time_axis is None and calendar not in {"standard", "gregorian", "proleptic_gregorian"}:
                raise ValueError(
                    f"{spec.slug} uses {calendar}; a native-calendar time axis is required"
                )
            if time_axis is not None and calendar != time_axis.calendar:
                aliases = {calendar, time_axis.calendar}
                if not aliases <= {"standard", "gregorian", "proleptic_gregorian"}:
                    raise ValueError(
                        f"{path} uses {calendar}, not the planned {time_axis.calendar} calendar"
                    )
            analysis_times = (
                time_axis.native_to_analysis(dataset.time.values)
                if time_axis is not None
                else pd.DatetimeIndex(pd.to_datetime(dataset.time.values))
            )
            selected = np.flatnonzero(
                (analysis_times >= read_start) & (analysis_times <= read_end)
            )
            if not len(selected):
                continue
            value = dataset[variable].isel(time=selected).assign_coords(
                time=analysis_times[selected]
            )
            value = _regional(value)
            if pressure_levels:
                value = _select_levels(value)
            parts.append(value.astype(np.float32).load())
    if not parts:
        raise ValueError(f"{variable} has no samples on analysis clock {read_start}..{read_end}")
    combined = xr.concat(parts, dim="time", coords="minimal", compat="override") if len(parts) > 1 else parts[0]
    combined = combined.sortby("time")
    times = pd.DatetimeIndex(pd.to_datetime(combined.time.values))
    _, keep = np.unique(times.view("int64"), return_index=True)
    return combined.isel(time=np.sort(keep)), paths


def _sample_grid(value: xr.DataArray) -> xr.DataArray:
    sampled = target_grid(value)
    return sampled.transpose("time", *(dimension for dimension in ("level", "latitude", "longitude") if dimension in sampled.dims))


def _interpolate(value: xr.DataArray, times: pd.DatetimeIndex, label: str) -> xr.DataArray:
    actual = pd.DatetimeIndex(pd.to_datetime(value.time.values))
    if actual.min() > times.min() or actual.max() < times.max():
        raise ValueError(f"{label} covers {actual.min()}..{actual.max()}, not {times.min()}..{times.max()}")
    coordinate = xr.DataArray(times, dims="time", coords={"time": times})
    return value.interp(time=coordinate, method="linear")


def _hourly_precipitation(value: xr.DataArray, times: pd.DatetimeIndex) -> xr.DataArray:
    units = str(value.attrs.get("units", "")).lower().replace(" ", "")
    if "s-1" not in units and "s**-1" not in units:
        raise ValueError(f"CMIP6 pr must be a flux in kg m-2 s-1, found {value.attrs.get('units')!r}")
    centres = times + pd.Timedelta(minutes=30)
    sampled = value.reindex(time=centres, method="nearest", tolerance=pd.Timedelta(minutes=91))
    sampled = sampled.assign_coords(time=times)
    if bool(sampled.isnull().all(("latitude", "longitude")).any()):
        raise ValueError("precipitation interval means do not cover every target hour")
    sampled = sampled.clip(min=0.0)
    sampled.attrs.update(value.attrs)
    return sampled


def _attrs(
    spec: RunSpec,
    month: str,
    temporal_basis: str,
    time_axis: TimeAxis | None,
) -> dict[str, str]:
    result = {
        "title": f"{spec.source_id} {spec.experiment_id} fields standardized for LPS tracking, {month}",
        "source": "CMIP6 via the CEDA/BADC archive",
        "activity_id": spec.activity,
        "source_id": spec.source_id,
        "experiment_id": spec.experiment_id,
        "variant_label": spec.member_id,
        "grid_label": spec.grid_label,
        "grid": "1 degree, 45--120E, 15S--45N",
        "temporal_basis": temporal_basis,
        "processing": "regional subset; bilinear spatial interpolation; frozen v5.6 detector input contract",
        "created_utc": utc_now(),
    }
    if time_axis is not None:
        result.update(
            {
                "source_calendar": time_axis.calendar,
                "time_coordinate_role": "ordinal analysis clock",
                "time_axis_basis": time_axis.basis,
                "native_anchor": time_axis.native_anchor,
                "analysis_anchor": time_axis.analysis_anchor,
            }
        )
    return result


def _require_time_axis(dataset: xr.Dataset, expected: pd.DatetimeIndex, label: str) -> None:
    actual = pd.DatetimeIndex(pd.to_datetime(dataset.time.values))
    if not actual.equals(expected):
        raise ValueError(
            f"{label} time axis has {len(actual)} values from {actual.min()} to {actual.max()}; "
            f"expected {len(expected)} from {expected.min()} to {expected.max()}"
        )


def validate_month(root: Path, month: str) -> dict[str, object]:
    """Validate the CMIP6-specific detector contract, including q or r."""

    start, end = month_bounds(month)
    expected_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    expected_three_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="3h")
    paths = standard_paths(root, month)
    contracts = {
        "vorticity": (("vo",), expected_hourly, ("time", "level", "latitude", "longitude")),
        "surface": (("msl", "sp", "u10", "v10"), expected_hourly, ("time", "latitude", "longitude")),
        "precipitation": (("mtpr",), expected_hourly, ("time", "latitude", "longitude")),
        "auxiliary": (("u", "v", "t", "q"), expected_three_hourly, ("time", "level", "latitude", "longitude")),
    }
    report: dict[str, object] = {"month": month, "status": "passed", "files": {}}
    for name, (variables, expected, dimensions) in contracts.items():
        path = paths[name]
        with xr.open_dataset(path) as dataset:
            require_variables(dataset, variables, path)
            _require_time_axis(dataset, expected, name)
            if dataset.sizes.get("latitude") != len(TARGET_LATS) or dataset.sizes.get("longitude") != len(TARGET_LONS):
                raise ValueError(f"{path} does not use the shared 1-degree grid")
            if "level" in dimensions and not np.allclose(dataset.level.values, PRESSURE_LEVELS):
                raise ValueError(f"{path} does not contain 850/700/500 hPa in canonical order")
            finite: dict[str, float] = {}
            for variable in variables:
                if dataset[variable].dims != dimensions:
                    raise ValueError(f"{path}:{variable} dimensions are {dataset[variable].dims}, expected {dimensions}")
                fraction = float(np.isfinite(dataset[variable].values).mean())
                if fraction < 0.75:
                    raise ValueError(f"{path}:{variable} finite fraction is only {fraction:.3f}")
                finite[variable] = round(fraction, 6)
        report["files"][name] = {"path": str(path), "finite_fraction": finite}
    return report


def validate_auxiliary_boundary(root: Path, timestamp: str | pd.Timestamp) -> dict[str, object]:
    boundary = pd.Timestamp(timestamp)
    month = boundary.strftime("%Y%m")
    path = standard_paths(root, month)["auxiliary"]
    with xr.open_dataset(path) as dataset:
        require_variables(dataset, ("u", "v", "t", "q"), path)
        _require_time_axis(dataset, pd.DatetimeIndex([boundary]), "auxiliary boundary")
        if dataset.sizes.get("latitude") != len(TARGET_LATS) or dataset.sizes.get("longitude") != len(TARGET_LONS):
            raise ValueError(f"{path} does not use the shared 1-degree grid")
        if not np.allclose(dataset.level.values, PRESSURE_LEVELS):
            raise ValueError(f"{path} does not contain 850/700/500 hPa in canonical order")
        finite = {
            variable: round(float(np.isfinite(dataset[variable].values).mean()), 6)
            for variable in ("u", "v", "t", "q")
        }
        if min(finite.values()) < 0.75:
            raise ValueError(f"{path} has insufficient finite boundary data: {finite}")
    return {"timestamp": boundary.isoformat(), "status": "passed", "path": str(path), "finite_fraction": finite}


def _source_records(paths: Iterable[Path]) -> list[dict[str, object]]:
    return [
        {
            "path": str(path),
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in sorted(set(paths))
    ]


def standardise_month(
    output_root: Path,
    badc_root: Path,
    spec: RunSpec,
    month: str,
    *,
    time_axis: TimeAxis | None = None,
) -> dict[str, object]:
    provenance = standard_paths(output_root, month)["provenance"]
    if provenance.is_file():
        try:
            cached = json.loads(provenance.read_text(encoding="utf-8"))
            expected_axis = time_axis.record() if time_axis is not None else None
            if cached.get("time_axis") != expected_axis:
                raise ValueError("cached standard month uses a different time-axis mapping")
            validate_month(output_root, month)
            return cached
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    start, end = month_bounds(month)
    hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")
    three_hourly = pd.date_range(start, end - pd.Timedelta(hours=1), freq="3h")
    read_end = end
    source_paths: list[Path] = []

    pressure: dict[str, xr.DataArray] = {}
    for variable in ("ua", "va", "ta", "hus"):
        value, paths = _open_variable(
            badc_root,
            spec,
            variable,
            start,
            read_end,
            pressure_levels=True,
            time_axis=time_axis,
        )
        pressure[variable] = _sample_grid(value)
        source_paths.extend(paths)
    surface: dict[str, xr.DataArray] = {}
    for variable in ("psl", "ps", "uas", "vas"):
        value, paths = _open_variable(
            badc_root,
            spec,
            variable,
            start,
            read_end,
            time_axis=time_axis,
        )
        surface[variable] = _sample_grid(value)
        source_paths.extend(paths)
    precipitation, paths = _open_variable(
        badc_root,
        spec,
        "pr",
        start,
        read_end,
        time_axis=time_axis,
    )
    precipitation = _sample_grid(precipitation)
    source_paths.extend(paths)

    u_hourly = _interpolate(pressure["ua"], hourly, "ua")
    v_hourly = _interpolate(pressure["va"], hourly, "va")
    vorticity = relative_vorticity_x1e5(
        np.asarray(u_hourly.values, dtype=np.float32),
        np.asarray(v_hourly.values, dtype=np.float32),
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
        attrs=_attrs(
            spec,
            month,
            "hourly interpolation of six-hourly instantaneous pressure-level winds",
            time_axis,
        ),
    )
    vorticity_output.vo.attrs.update({"long_name": "relative vorticity", "units": "10^-5 s^-1"})

    surface_output = xr.Dataset(
        {
            "msl": _interpolate(surface["psl"], hourly, "psl"),
            "sp": _interpolate(surface["ps"], hourly, "ps"),
            "u10": _interpolate(surface["uas"], hourly, "uas"),
            "v10": _interpolate(surface["vas"], hourly, "vas"),
        },
        attrs=_attrs(
            spec,
            month,
            "hourly linear interpolation of three- or six-hourly source surface fields; "
            "CMIP6 point/mean semantics retained in each variable's cell_methods attribute",
            time_axis,
        ),
    ).astype(np.float32)
    for name, long_name, units in (
        ("msl", "mean sea level pressure", "Pa"),
        ("sp", "surface pressure", "Pa"),
        ("u10", "10 m eastward wind", "m s-1"),
        ("v10", "10 m northward wind", "m s-1"),
    ):
        surface_output[name].attrs.update({"long_name": long_name, "units": units})

    rain = _hourly_precipitation(precipitation, hourly)
    precipitation_output = xr.Dataset(
        {"mtpr": rain.transpose("time", "latitude", "longitude")},
        attrs=_attrs(
            spec,
            month,
            "native three-hour precipitation interval means assigned to hourly bins",
            time_axis,
        ),
    ).astype(np.float32)
    precipitation_output.mtpr.attrs.update({"long_name": "total precipitation rate", "units": "kg m-2 s-1"})

    auxiliary_output = xr.Dataset(
        {
            "u": _interpolate(pressure["ua"], three_hourly, "ua"),
            "v": _interpolate(pressure["va"], three_hourly, "va"),
            "t": _interpolate(pressure["ta"], three_hourly, "ta"),
            "q": _interpolate(pressure["hus"], three_hourly, "hus"),
        },
        attrs=_attrs(
            spec,
            month,
            "three-hour interpolation of six-hourly pressure-level fields",
            time_axis,
        ),
    ).astype(np.float32)
    for name in ("u", "v"):
        auxiliary_output[name].attrs["units"] = "m s-1"
    auxiliary_output.t.attrs["units"] = "K"
    auxiliary_output.q.attrs["units"] = "kg kg-1"

    paths = standard_paths(output_root, month)
    for name, dataset in (
        ("vorticity", vorticity_output),
        ("surface", surface_output),
        ("precipitation", precipitation_output),
        ("auxiliary", auxiliary_output),
    ):
        atomic_to_netcdf(dataset, paths[name])

    report: dict[str, object] = {
        "schema": STANDARD_SCHEMA,
        "created_utc": utc_now(),
        "run": spec.__dict__,
        "month": month,
        "coverage": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "time_axis": time_axis.record() if time_axis is not None else None,
        "source_fields": {
            variable: {
                "table_id": field_table(spec, variable),
                "cell_methods": str(value.attrs.get("cell_methods", "not declared")),
            }
            for variable, value in {
                **pressure,
                **surface,
                "pr": precipitation,
            }.items()
        },
        "source_files": _source_records(source_paths),
        "outputs": {},
    }
    for name in ("vorticity", "surface", "precipitation", "auxiliary"):
        path = paths[name]
        report["outputs"][name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    paths["provenance"].parent.mkdir(parents=True, exist_ok=True)
    temporary = paths["provenance"].with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, paths["provenance"])
    validate_month(output_root, month)
    return report


def standardise_auxiliary_boundary(
    output_root: Path,
    badc_root: Path,
    spec: RunSpec,
    timestamp: str | pd.Timestamp,
    *,
    time_axis: TimeAxis | None = None,
) -> dict[str, object]:
    """Write the single next-month pressure-level frame needed at a run boundary."""

    boundary = pd.Timestamp(timestamp)
    month = boundary.strftime("%Y%m")
    path = standard_paths(output_root, month)["auxiliary"]
    provenance = output_root / "standard" / "provenance" / f"pl3h-boundary-{boundary:%Y%m%d%H}.json"
    if provenance.is_file():
        try:
            cached = json.loads(provenance.read_text(encoding="utf-8"))
            expected_axis = time_axis.record() if time_axis is not None else None
            if cached.get("time_axis") != expected_axis:
                raise ValueError("cached auxiliary boundary uses a different time-axis mapping")
            validate_auxiliary_boundary(output_root, boundary)
            return cached
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    target = pd.DatetimeIndex([boundary])
    pressure: dict[str, xr.DataArray] = {}
    source_paths: list[Path] = []
    for variable in ("ua", "va", "ta", "hus"):
        value, paths = _open_variable(
            badc_root,
            spec,
            variable,
            boundary,
            boundary,
            pressure_levels=True,
            time_axis=time_axis,
        )
        pressure[variable] = _sample_grid(value)
        source_paths.extend(paths)
    output = xr.Dataset(
        {
            "u": _interpolate(pressure["ua"], target, "ua"),
            "v": _interpolate(pressure["va"], target, "va"),
            "t": _interpolate(pressure["ta"], target, "ta"),
            "q": _interpolate(pressure["hus"], target, "hus"),
        },
        attrs=_attrs(
            spec,
            month,
            "single pressure-level boundary frame for final-month interpolation",
            time_axis,
        ),
    ).astype(np.float32)
    for name in ("u", "v"):
        output[name].attrs["units"] = "m s-1"
    output.t.attrs["units"] = "K"
    output.q.attrs["units"] = "kg kg-1"
    atomic_to_netcdf(output, path)
    report: dict[str, object] = {
        "schema": "lps-atlas-cmip6-standard-auxiliary-boundary-v1",
        "created_utc": utc_now(),
        "run": spec.__dict__,
        "timestamp": boundary.isoformat(),
        "time_axis": time_axis.record() if time_axis is not None else None,
        "source_files": _source_records(source_paths),
        "output": {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)},
    }
    provenance.parent.mkdir(parents=True, exist_ok=True)
    temporary = provenance.with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, provenance)
    validate_auxiliary_boundary(output_root, boundary)
    return report


def run_spec(args: argparse.Namespace) -> RunSpec:
    return RunSpec(
        activity=args.activity,
        institution=args.institution,
        source_id=args.source_id,
        experiment_id=args.experiment_id,
        member_id=args.member_id,
        grid_label=args.grid_label,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--badc-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--activity", required=True)
    parser.add_argument("--institution", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--member-id", required=True)
    parser.add_argument("--grid-label", default="gn")
    parser.add_argument("--time-axis", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    standardise = subparsers.add_parser("standardise-month")
    standardise.add_argument("--month", required=True, help="YYYYMM")
    boundary = subparsers.add_parser("standardise-aux-boundary")
    boundary.add_argument("--timestamp", required=True, help="Gregorian timestamp, normally YYYY-MM-01T00:00")
    validate = subparsers.add_parser("validate-month")
    validate.add_argument("--month", required=True, help="YYYYMM")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    axis = load_time_axis(args.time_axis)
    if args.command == "standardise-month":
        report = standardise_month(
            args.output_root,
            args.badc_root,
            run_spec(args),
            args.month,
            time_axis=axis,
        )
    elif args.command == "standardise-aux-boundary":
        report = standardise_auxiliary_boundary(
            args.output_root,
            args.badc_root,
            run_spec(args),
            args.timestamp,
            time_axis=axis,
        )
    else:
        report = validate_month(args.output_root, args.month)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
