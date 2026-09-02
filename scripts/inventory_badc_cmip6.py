#!/usr/bin/env python3
"""Inventory high-frequency BADC CMIP6 fields usable by the LPS tracker.

The inventory is deliberately stricter than a directory listing.  It groups
files at model/experiment/member grain, checks fixed pressure-level content in
sample files, and distinguishes an immediately trackable pressure-wind/MSLP
combination from hybrid-level data that still needs vertical interpolation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import xarray as xr


DEFAULT_ROOT = Path("/badc/cmip6/data/CMIP6")
DEFAULT_ACTIVITIES = ("CMIP", "ScenarioMIP", "HighResMIP")
TABLES = ("3hr", "6hrPlevPt", "6hrPlev", "6hrLev")
FIXED_PRESSURE_TABLES = ("6hrPlevPt", "6hrPlev")
TARGET_VARIABLES = frozenset(
    {
        "ua",
        "va",
        "vo",
        "ta",
        "hur",
        "hus",
        "psl",
        "ps",
        "uas",
        "vas",
        "pr",
        "zg",
    }
)
PERIOD_RE = re.compile(r"_(\d{4,14})-(\d{4,14})\.nc$")
VERSION_RE = re.compile(r"v\d{8,14}$")
CORE_LEVELS_HPA = np.asarray([850.0, 700.0, 500.0])
VARIABLE_COLUMNS = (
    "activity",
    "institution",
    "source_id",
    "experiment_id",
    "member_id",
    "table_id",
    "variable_id",
    "grid_label",
    "version",
    "file_count",
    "bytes",
    "first_period",
    "last_period",
    "sample_file",
    "sample_cadence_hours",
    "calendar",
    "latitude_count",
    "longitude_count",
    "latitude_spacing_deg",
    "longitude_spacing_deg",
    "pressure_levels_hpa",
    "has_850_700_500_hpa",
    "metadata_error",
    "path",
)
RUN_COLUMNS = (
    "activity",
    "institution",
    "source_id",
    "experiment_id",
    "member_id",
    "candidate_ready",
    "full_physics_ready",
    "trackability",
    "circulation_basis",
    "pressure_levels_verified",
    "has_mslp",
    "has_surface_wind",
    "has_precipitation",
    "has_temperature",
    "has_humidity",
    "has_surface_pressure",
    "historical_pair_available",
    "grid_labels",
    "first_period",
    "last_period",
    "available_fields",
    "missing_for_candidate_tracking",
    "remaining_preprocessing",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def children(path: Path) -> list[Path]:
    try:
        with os.scandir(path) as entries:
            return sorted(
                (Path(entry.path) for entry in entries if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=True)),
                key=lambda value: value.name,
            )
    except (FileNotFoundError, PermissionError):
        return []


def selected_version(grid_path: Path) -> tuple[str, Path] | None:
    latest = grid_path / "latest"
    if latest.is_dir():
        try:
            target = latest.resolve(strict=True)
            return target.name, latest
        except (OSError, RuntimeError):
            pass
    versions = [path for path in children(grid_path) if VERSION_RE.fullmatch(path.name)]
    return (versions[-1].name, versions[-1]) if versions else None


def netcdf_files(path: Path) -> list[Path]:
    try:
        with os.scandir(path) as entries:
            return sorted(
                Path(entry.path)
                for entry in entries
                if entry.is_file(follow_symlinks=True) and entry.name.endswith(".nc")
            )
    except (FileNotFoundError, PermissionError):
        return []


def period_bounds(files: Iterable[Path]) -> tuple[str, str]:
    starts: list[str] = []
    ends: list[str] = []
    for path in files:
        match = PERIOD_RE.search(path.name)
        if match:
            starts.append(match.group(1))
            ends.append(match.group(2))
    return (min(starts) if starts else "", max(ends) if ends else "")


def coordinate(dataset: xr.Dataset, names: Iterable[str]) -> xr.DataArray | None:
    for name in names:
        if name in dataset.coords:
            return dataset.coords[name]
        if name in dataset.variables and dataset[name].ndim == 1:
            return dataset[name]
    return None


def spacing(values: np.ndarray) -> float | None:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size < 2:
        return None
    return round(float(np.nanmedian(np.abs(np.diff(finite)))), 5)


def cadence_hours(time: xr.DataArray | None) -> float | None:
    if time is None or time.size < 2:
        return None
    values = np.asarray(time.values, dtype=float)
    step = float(np.nanmedian(np.diff(values)))
    units = str(time.attrs.get("units", "")).lower()
    if units.startswith("day"):
        step *= 24.0
    elif units.startswith("minute"):
        step /= 60.0
    elif units.startswith("second"):
        step /= 3600.0
    return round(step, 6) if np.isfinite(step) else None


def sample_metadata(path: Path, variable: str, table: str) -> dict[str, Any]:
    output: dict[str, Any] = {
        "sample_cadence_hours": None,
        "calendar": "",
        "latitude_count": None,
        "longitude_count": None,
        "latitude_spacing_deg": None,
        "longitude_spacing_deg": None,
        "pressure_levels_hpa": "",
        "has_850_700_500_hpa": None,
        "metadata_error": "",
    }
    try:
        with xr.open_dataset(path, decode_times=False) as dataset:
            time = coordinate(dataset, ("time", "t"))
            latitude = coordinate(dataset, ("lat", "latitude", "nav_lat"))
            longitude = coordinate(dataset, ("lon", "longitude", "nav_lon"))
            output.update(
                {
                    "sample_cadence_hours": cadence_hours(time),
                    "calendar": str(time.attrs.get("calendar", "standard")) if time is not None else "",
                    "latitude_count": int(latitude.size) if latitude is not None else None,
                    "longitude_count": int(longitude.size) if longitude is not None else None,
                    "latitude_spacing_deg": spacing(latitude.values) if latitude is not None and latitude.ndim == 1 else None,
                    "longitude_spacing_deg": spacing(longitude.values) if longitude is not None and longitude.ndim == 1 else None,
                }
            )
            if table in FIXED_PRESSURE_TABLES and variable in {"ua", "va", "vo", "ta", "hur", "hus", "zg"}:
                level = coordinate(dataset, ("plev", "level", "lev", "p"))
                if level is None:
                    output["has_850_700_500_hpa"] = False
                else:
                    values = np.asarray(level.values, dtype=float).reshape(-1)
                    units = str(level.attrs.get("units", "")).lower()
                    if "pa" in units and "hpa" not in units:
                        values = values / 100.0
                    elif np.nanmedian(np.abs(values)) > 2_000:
                        values = values / 100.0
                    rounded = sorted({round(float(value), 4) for value in values if np.isfinite(value)})
                    output["pressure_levels_hpa"] = "|".join(f"{value:g}" for value in rounded)
                    output["has_850_700_500_hpa"] = bool(
                        all(np.nanmin(np.abs(values - wanted)) <= 0.5 for wanted in CORE_LEVELS_HPA)
                    )
    except Exception as error:  # the error is retained as inventory evidence
        output["metadata_error"] = f"{type(error).__name__}: {error}"
    return output


def sample_available_metadata(files: list[Path], variable: str, table: str) -> dict[str, Any]:
    """Inspect a second segment when the first CMIP file is unreadable."""
    result = sample_metadata(files[0], variable, table)
    if result["metadata_error"] and len(files) > 1:
        fallback = sample_metadata(files[-1], variable, table)
        if not fallback["metadata_error"]:
            return fallback
        result["metadata_error"] = f"{result['metadata_error']} | fallback: {fallback['metadata_error']}"
    return result


def variable_rows(root: Path, activities: Iterable[str], *, inspect_metadata: bool) -> Iterator[dict[str, Any]]:
    # CMIP6 variables in one run/table/grid share the same time, horizontal
    # and pressure coordinates.  Opening every variable's first file on the
    # BADC filesystem is needlessly expensive, so inspect one representative
    # and reuse only this structural metadata at that exact DRS grain.
    metadata_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    for activity_name in activities:
        activity = root / activity_name
        for institution in children(activity):
            for source in children(institution):
                for experiment in children(source):
                    for member in children(experiment):
                        for table_name in TABLES:
                            table = member / table_name
                            if not table.is_dir():
                                continue
                            for variable in children(table):
                                if variable.name not in TARGET_VARIABLES:
                                    continue
                                for grid in children(variable):
                                    selection = selected_version(grid)
                                    if selection is None:
                                        continue
                                    version, version_path = selection
                                    files = netcdf_files(version_path)
                                    if not files:
                                        continue
                                    first, last = period_bounds(files)
                                    cache_key = (
                                        activity_name,
                                        institution.name,
                                        source.name,
                                        experiment.name,
                                        member.name,
                                        table_name,
                                        grid.name,
                                        variable.name if table_name in FIXED_PRESSURE_TABLES and variable.name in {"ua", "va", "vo"} else "shared-structure",
                                    )
                                    if inspect_metadata and cache_key not in metadata_cache:
                                        metadata_cache[cache_key] = sample_available_metadata(files, variable.name, table_name)
                                    metadata = dict(metadata_cache[cache_key]) if inspect_metadata else {
                                        "sample_cadence_hours": None,
                                        "calendar": "",
                                        "latitude_count": None,
                                        "longitude_count": None,
                                        "latitude_spacing_deg": None,
                                        "longitude_spacing_deg": None,
                                        "pressure_levels_hpa": "",
                                        "has_850_700_500_hpa": None,
                                        "metadata_error": "not inspected",
                                    }
                                    yield {
                                        "activity": activity_name,
                                        "institution": institution.name,
                                        "source_id": source.name,
                                        "experiment_id": experiment.name,
                                        "member_id": member.name,
                                        "table_id": table_name,
                                        "variable_id": variable.name,
                                        "grid_label": grid.name,
                                        "version": version,
                                        "file_count": len(files),
                                        "bytes": sum(path.stat().st_size for path in files),
                                        "first_period": first,
                                        "last_period": last,
                                        "sample_file": str(files[0]),
                                        **metadata,
                                        "path": str(version_path),
                                    }


def paired(rows: list[dict[str, Any]], table: str, left: str, right: str) -> tuple[bool, bool]:
    a = [row for row in rows if row["table_id"] == table and row["variable_id"] == left]
    b = [row for row in rows if row["table_id"] == table and row["variable_id"] == right]
    present = bool(a and b)
    verified = present and any(row["has_850_700_500_hpa"] is True for row in a) and any(
        row["has_850_700_500_hpa"] is True for row in b
    )
    return present, verified


def run_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(str(row[name]) for name in ("activity", "institution", "source_id", "experiment_id", "member_id"))
        groups[key].append(row)

    output: list[dict[str, Any]] = []
    for key, available in sorted(groups.items()):
        fields = {(str(row["table_id"]), str(row["variable_id"])) for row in available}
        fixed_present = False
        fixed_verified = False
        circulation_basis = "none"
        for table in FIXED_PRESSURE_TABLES:
            uv_present, uv_verified = paired(available, table, "ua", "va")
            vo = [row for row in available if row["table_id"] == table and row["variable_id"] == "vo"]
            vo_present = bool(vo)
            vo_verified = any(row["has_850_700_500_hpa"] is True for row in vo)
            if uv_verified:
                fixed_present = fixed_verified = True
                circulation_basis = f"{table}:ua+va"
                break
            if vo_verified:
                fixed_present = fixed_verified = True
                circulation_basis = f"{table}:vo"
                break
            if uv_present or vo_present:
                fixed_present = True
                circulation_basis = f"{table}:{'ua+va' if uv_present else 'vo'}"
        hybrid_present, unused_hybrid_verified = paired(available, "6hrLev", "ua", "va")
        variables = {str(row["variable_id"]) for row in available}
        has_mslp = "psl" in variables
        has_surface_wind = {"uas", "vas"}.issubset(variables)
        has_precipitation = "pr" in variables
        has_temperature = any((table, "ta") in fields for table in FIXED_PRESSURE_TABLES)
        has_humidity = any((table, variable) in fields for table in FIXED_PRESSURE_TABLES for variable in ("hur", "hus"))
        has_surface_pressure = "ps" in variables
        candidate_ready = fixed_verified and has_mslp
        if candidate_ready:
            trackability = "candidate_ready"
        elif fixed_present and has_mslp:
            trackability = "candidate_fields_present_levels_unverified"
        elif hybrid_present and has_mslp and has_surface_pressure:
            trackability = "hybrid_levels_convertible"
            circulation_basis = "6hrLev:ua+va"
        elif fixed_present and not has_mslp:
            trackability = "pressure_circulation_without_mslp"
        else:
            trackability = "incomplete"
        full_physics = bool(
            candidate_ready
            and has_surface_wind
            and has_precipitation
            and has_temperature
            and has_humidity
        )
        missing: list[str] = []
        if not fixed_verified:
            missing.append("verified fixed-pressure ua+va or vo at 850/700/500 hPa")
        if not has_mslp:
            missing.append("six-hourly-or-better psl")
        preprocessing: list[str] = ["regional subset and common-grid standardisation"]
        if trackability == "hybrid_levels_convertible":
            preprocessing.insert(0, "hybrid-to-pressure interpolation")
        if not has_surface_wind:
            preprocessing.append("classification without native 10-m vector wind")
        if not has_precipitation:
            preprocessing.append("precipitation diagnostics unavailable")
        output.append(
            {
                **dict(zip(("activity", "institution", "source_id", "experiment_id", "member_id"), key, strict=True)),
                "candidate_ready": candidate_ready,
                "full_physics_ready": full_physics,
                "trackability": trackability,
                "circulation_basis": circulation_basis,
                "pressure_levels_verified": fixed_verified,
                "has_mslp": has_mslp,
                "has_surface_wind": has_surface_wind,
                "has_precipitation": has_precipitation,
                "has_temperature": has_temperature,
                "has_humidity": has_humidity,
                "has_surface_pressure": has_surface_pressure,
                "historical_pair_available": False,
                "grid_labels": "|".join(sorted({str(row["grid_label"]) for row in available})),
                "first_period": min((str(row["first_period"]) for row in available if row["first_period"]), default=""),
                "last_period": max((str(row["last_period"]) for row in available if row["last_period"]), default=""),
                "available_fields": "|".join(f"{table}:{variable}" for table, variable in sorted(fields)),
                "missing_for_candidate_tracking": "|".join(missing),
                "remaining_preprocessing": "|".join(preprocessing),
            }
        )

    historical = {
        (row["source_id"], row["member_id"])
        for row in output
        if row["experiment_id"] in {"historical", "hist-1950"} and row["candidate_ready"]
    }
    for row in output:
        if row["experiment_id"] not in {"historical", "hist-1950"}:
            row["historical_pair_available"] = (row["source_id"], row["member_id"]) in historical
    return output


def write_csv(path: Path, rows: list[dict[str, Any]], columns: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def build_summary(root: Path, variables: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row["trackability"]) for row in runs)
    ready = [row for row in runs if row["candidate_ready"]]
    paired_ready = [row for row in ready if row["historical_pair_available"]]
    return {
        "schema": "lps-atlas-cmip6-badc-inventory-v1",
        "generated_utc": utc_now(),
        "root": str(root),
        "grain": "activity / institution / source_id / experiment_id / member_id",
        "candidate_ready_definition": "Verified 850/700/500-hPa ua+va or vo at <=6-hour cadence, plus <=6-hour psl; all fields still require common-grid standardisation.",
        "full_physics_ready_definition": "Candidate-ready plus 10-m vector wind, precipitation, pressure-level temperature and humidity.",
        "limitations": [
            "Coverage bounds come from CMIP filenames; every timestep is not decoded in this filesystem inventory.",
            "A candidate-ready run still requires a month-scale pilot for calendars, missing timesteps, grid orientation and units before tracking.",
            "Hybrid-level runs are not called ready until model-level coefficients and surface pressure have been validated for pressure interpolation.",
        ],
        "counts": {
            "variable_records": len(variables),
            "metadata_error_records": sum(bool(row["metadata_error"]) for row in variables),
            "runs": len(runs),
            "institutions": len({row["institution"] for row in runs}),
            "models": len({row["source_id"] for row in runs}),
            "candidate_ready_runs": len(ready),
            "full_physics_ready_runs": sum(bool(row["full_physics_ready"]) for row in runs),
            "scenario_runs_with_ready_historical_pair": len(paired_ready),
            "trackability": dict(sorted(statuses.items())),
        },
        "ready_models": sorted({str(row["source_id"]) for row in ready}),
        "ready_experiments": dict(sorted(Counter(str(row["experiment_id"]) for row in ready).items())),
        "recommended_first_wave": [
            {
                "source_id": row["source_id"],
                "experiment_id": row["experiment_id"],
                "member_id": row["member_id"],
                "full_physics_ready": row["full_physics_ready"],
                "historical_pair_available": row["historical_pair_available"],
            }
            for row in ready
            if row["experiment_id"] in {"historical", "hist-1950", "ssp126", "ssp245", "ssp370", "ssp585", "highres-future"}
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=Path("data/cmip6-inventory"))
    parser.add_argument("--activities", nargs="+", default=list(DEFAULT_ACTIVITIES))
    parser.add_argument("--no-inspect-metadata", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    variables = list(variable_rows(args.root, args.activities, inspect_metadata=not args.no_inspect_metadata))
    runs = run_rows(variables)
    write_csv(args.output / "badc-cmip6-fields.csv", variables, VARIABLE_COLUMNS)
    write_csv(args.output / "badc-cmip6-trackability.csv", runs, RUN_COLUMNS)
    summary = build_summary(args.root, variables, runs)
    destination = args.output / "badc-cmip6-summary.json"
    temporary = destination.with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    print(json.dumps(summary["counts"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
