#!/usr/bin/env python3
"""Calculate and publish model-paired global-mean ``tas`` warming diagnostics."""

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
import xarray as xr

from reanalysis_pipeline.common import sha256

from .source import DEFAULT_ROOT, RunSpec, files_overlapping_stamps


SCHEMA = "lps-atlas-cmip6-global-warming-v1"
INDEX_SCHEMA = "lps-atlas-cmip6-climate-index-v1"
DEFAULT_GWL_TABLE = Path("data/cmip6-inventory/ipcc-ar6-gwl-crossings.json")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_gzip_json(path: Path, value: Any) -> None:
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_bytes(compressed)
    os.replace(temporary, path)


def _load_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _coordinate_bounds(dataset: xr.Dataset, coordinate_name: str) -> np.ndarray:
    coordinate = dataset[coordinate_name]
    bounds_name = coordinate.attrs.get("bounds")
    if bounds_name and bounds_name in dataset:
        bounds = np.asarray(dataset[bounds_name].transpose(coordinate_name, ...).values, dtype=float)
        if bounds.ndim == 2 and bounds.shape == (coordinate.size, 2):
            return bounds
    values = np.asarray(coordinate.values, dtype=float)
    if values.ndim != 1 or len(values) < 2:
        raise ValueError(f"cannot derive bounds for {coordinate_name}")
    midpoint = (values[:-1] + values[1:]) / 2.0
    edges = np.concatenate(
        ([values[0] - (midpoint[0] - values[0])], midpoint, [values[-1] + (values[-1] - midpoint[-1])])
    )
    if coordinate_name == "lat":
        edges = np.clip(edges, -90.0, 90.0)
    return np.column_stack((edges[:-1], edges[1:]))


def spherical_cell_weights(dataset: xr.Dataset) -> xr.DataArray:
    """Return exact relative spherical areas for a rectilinear latitude/longitude grid."""

    if "lat" not in dataset.coords or "lon" not in dataset.coords:
        raise ValueError("Amon tas must expose lat and lon coordinates")
    if dataset.lat.ndim != 1 or dataset.lon.ndim != 1:
        raise ValueError("warming calculation currently requires a rectilinear atmospheric grid")
    latitude_bounds = _coordinate_bounds(dataset, "lat")
    longitude_bounds = _coordinate_bounds(dataset, "lon")
    latitude_factor = np.abs(
        np.sin(np.deg2rad(latitude_bounds[:, 1]))
        - np.sin(np.deg2rad(latitude_bounds[:, 0]))
    )
    longitude_span = np.abs(longitude_bounds[:, 1] - longitude_bounds[:, 0])
    longitude_span = np.minimum(longitude_span, 360.0 - np.minimum(longitude_span, 360.0))
    longitude_factor = np.deg2rad(longitude_span)
    weights = np.outer(latitude_factor, longitude_factor)
    if not np.isfinite(weights).all() or float(weights.sum()) <= 0:
        raise ValueError("invalid spherical cell weights")
    return xr.DataArray(
        weights,
        coords={"lat": dataset.lat, "lon": dataset.lon},
        dims=("lat", "lon"),
        name="relative_cell_area",
    )


def _duration_days(value: Any) -> float:
    if isinstance(value, np.timedelta64):
        return float(value / np.timedelta64(1, "D"))
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds() / 86400.0)
    raise TypeError(f"unsupported decoded time-bound interval {type(value)!r}")


def _time_weights(dataset: xr.Dataset, indices: np.ndarray) -> np.ndarray:
    bounds_name = dataset.time.attrs.get("bounds")
    if bounds_name and bounds_name in dataset:
        bounds = dataset[bounds_name].isel(time=indices).transpose("time", ...).values
        if bounds.ndim == 2 and bounds.shape[1] == 2:
            values = np.asarray([_duration_days(row[1] - row[0]) for row in bounds], dtype=float)
            if np.isfinite(values).all() and np.all(values > 0):
                return values
    return np.asarray(dataset.time.isel(time=indices).dt.days_in_month.values, dtype=float)


def summarise_period(period_plan: Path, badc_root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    plan = json.loads(period_plan.read_text(encoding="utf-8"))
    run = plan["run"]
    spec = RunSpec(
        run["activity"],
        run["institution"],
        run["source_id"],
        run["experiment_id"],
        run["member_id"],
        run.get("grid_label", "gn"),
    )
    start = str(plan["core_start"])
    end = str(plan["core_end"])
    start_ym, end_ym = int(start[:6]), int(end[:6])
    directory = spec.field_directory(badc_root, "Amon", "tas")
    source_files = files_overlapping_stamps(directory, start, end)
    monthly: dict[int, tuple[float, float]] = {}
    grid_shape: tuple[int, int] | None = None
    units: str | None = None
    calendar: str | None = None

    time_decoder = xr.coders.CFDatetimeCoder(use_cftime=True)
    for source in source_files:
        with xr.open_dataset(source, decode_times=time_decoder) as dataset:
            if "tas" not in dataset:
                raise ValueError(f"tas is absent from {source}")
            field = dataset.tas
            if not {"time", "lat", "lon"}.issubset(field.dims):
                raise ValueError(f"tas has unsupported dimensions {field.dims} in {source}")
            this_units = str(field.attrs.get("units", ""))
            if this_units not in {"K", "kelvin"}:
                raise ValueError(f"tas units are not kelvin in {source}: {this_units}")
            units = this_units
            calendar = str(dataset.time.encoding.get("calendar") or dataset.time.attrs.get("calendar") or "standard")
            years = np.asarray(dataset.time.dt.year.values, dtype=int)
            months = np.asarray(dataset.time.dt.month.values, dtype=int)
            year_month = years * 100 + months
            indices = np.flatnonzero((year_month >= start_ym) & (year_month <= end_ym))
            if not len(indices):
                continue
            weights = spherical_cell_weights(dataset)
            selected = field.isel(time=indices)
            valid_weights = weights.where(selected.notnull())
            numerator = (selected * weights).sum(("lat", "lon"), skipna=True)
            denominator = valid_weights.sum(("lat", "lon"), skipna=True)
            means = np.asarray((numerator / denominator).load().values, dtype=float)
            durations = _time_weights(dataset, indices)
            grid_shape = (int(dataset.sizes["lat"]), int(dataset.sizes["lon"]))
            for offset, index in enumerate(indices):
                key = int(year_month[index])
                if key in monthly:
                    raise ValueError(f"duplicate Amon tas month {key} across source files")
                monthly[key] = (float(means[offset]), float(durations[offset]))

    expected = []
    year, month = divmod(start_ym, 100)
    end_year, end_month = divmod(end_ym, 100)
    while (year, month) <= (end_year, end_month):
        expected.append(year * 100 + month)
        month += 1
        if month == 13:
            year += 1
            month = 1
    missing = sorted(set(expected) - set(monthly))
    unexpected = sorted(set(monthly) - set(expected))
    if missing or unexpected:
        raise ValueError(f"tas month coverage mismatch; missing={missing[:6]}, unexpected={unexpected[:6]}")
    values = np.asarray([monthly[key][0] for key in expected], dtype=float)
    durations = np.asarray([monthly[key][1] for key in expected], dtype=float)
    if not np.isfinite(values).all() or not np.isfinite(durations).all():
        raise ValueError("non-finite global tas monthly means or weights")
    annual = []
    for selected_year in range(int(start[:4]), int(end[:4]) + 1):
        locations = [position for position, key in enumerate(expected) if key // 100 == selected_year]
        annual.append(
            {
                "year": selected_year,
                "mean_tas_k": float(np.average(values[locations], weights=durations[locations])),
            }
        )
    return {
        "run": {
            "activity": spec.activity,
            "institution": spec.institution,
            "source_id": spec.source_id,
            "experiment_id": spec.experiment_id,
            "member_id": spec.member_id,
            "grid_label": spec.grid_label,
        },
        "period": {"start": start, "end": end, "months": len(expected)},
        "mean_tas_k": float(np.average(values, weights=durations)),
        "annual_mean_tas_k": annual,
        "calendar": calendar,
        "units": units,
        "grid_shape": list(grid_shape) if grid_shape else None,
        "source": {
            "table_id": "Amon",
            "variable_id": "tas",
            "directory": str(directory),
            "files": [path.name for path in source_files],
        },
    }


def _pair_id(historical: dict[str, Any], future: dict[str, Any]) -> str:
    identity = (
        historical["run"]["source_id"],
        historical["run"]["member_id"],
        historical["run"]["experiment_id"],
        future["run"]["experiment_id"],
    )
    return hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:16]


def summarise_pair(run_root: Path, badc_root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    plan = json.loads((run_root / "plan.json").read_text(encoding="utf-8"))
    period_paths = [run_root / item["source_label"] / "period-plan.json" for item in plan["periods"]]
    records = [summarise_period(path, badc_root) for path in period_paths]
    historical = next((record for record in records if record["run"]["experiment_id"] == "historical"), None)
    future = next((record for record in records if record["run"]["experiment_id"] != "historical"), None)
    if historical is None or future is None or len(records) != 2:
        raise ValueError(f"{run_root} must contain exactly one historical and one future period")
    if historical["run"]["source_id"] != future["run"]["source_id"]:
        raise ValueError("warming pair uses different source models")
    if historical["run"]["member_id"] != future["run"]["member_id"]:
        raise ValueError("warming pair uses different members")
    change = float(future["mean_tas_k"] - historical["mean_tas_k"])
    if not np.isfinite(change) or change <= 0:
        raise ValueError(f"expected positive late-century warming, found {change}")
    return {
        "id": _pair_id(historical, future),
        "source_label": historical["run"]["source_id"],
        "member_id": historical["run"]["member_id"],
        "future_experiment_id": future["run"]["experiment_id"],
        "historical": historical,
        "future": future,
        "change_k": change,
    }


def _attach_published_gwl(record: dict[str, Any], table: dict[str, Any]) -> None:
    match = next(
        (
            run
            for run in table.get("runs", [])
            if run.get("source_id") == record["source_label"]
            and run.get("member_id") == record["member_id"]
        ),
        None,
    )
    if match is None:
        return
    levels = match.get("scenarios", {}).get(record["future_experiment_id"])
    if not isinstance(levels, dict):
        return
    crossings = []
    for label in ("1.5", "2", "3", "4"):
        central_year = levels.get(label)
        crossings.append(
            {
                "level_c": float(label),
                "central_year": int(central_year) if central_year is not None else None,
                "window_start_year": int(central_year) - 9 if central_year is not None else None,
                "window_end_year": int(central_year) + 10 if central_year is not None else None,
            }
        )
    record["published_gwl"] = {
        "scenario": record["future_experiment_id"],
        "baseline": table["definition"]["baseline"],
        "window_years": table["definition"]["window_years"],
        "crossings": crossings,
    }


def build_registry(
    run_roots: list[Path],
    output_dir: Path,
    badc_root: Path = DEFAULT_ROOT,
    gwl_table: Path | None = DEFAULT_GWL_TABLE,
) -> Path:
    pairs = [summarise_pair(path.resolve(), badc_root) for path in run_roots]
    published_gwl = None
    if gwl_table is not None:
        published_gwl = json.loads(gwl_table.read_text(encoding="utf-8"))
        if published_gwl.get("schema") != "lps-atlas-ipcc-ar6-gwl-crossings-v1":
            raise ValueError(f"unsupported published GWL table: {gwl_table}")
        for record in pairs:
            _attach_published_gwl(record, published_gwl)
    by_id = {record["id"]: record for record in pairs}
    if len(by_id) != len(pairs):
        raise ValueError("duplicate CMIP6 warming pair identity")
    payload = {
        "schema": SCHEMA,
        "generated_utc": utc_now(),
        "definition": (
            "Future minus historical all-month global-mean near-surface air temperature "
            "over the exact paired track-analysis windows."
        ),
        "method": (
            "CMIP6 Amon tas from the same model, member and experiment as each track run; "
            "exact spherical latitude-longitude cell areas and decoded monthly time-bound durations."
        ),
        "published_gwl": (
            {
                "source": published_gwl["source"],
                "definition": published_gwl["definition"],
            }
            if published_gwl is not None else None
        ),
        "pairs": sorted(pairs, key=lambda record: (record["source_label"], record["future_experiment_id"])),
    }
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    asset = output_dir / f"climate-warming.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
    _atomic_gzip_json(asset, payload)
    manifest = output_dir / "manifest.json"
    _atomic_json(
        manifest,
        {
            "schema": SCHEMA,
            "generated_utc": payload["generated_utc"],
            "asset": {"path": asset.name, "sha256": sha256(asset), "bytes": asset.stat().st_size},
            "pairs": len(pairs),
        },
    )
    return manifest


def attach_to_climate_bundle(climate_manifest: Path, warming_manifest: Path) -> Path:
    manifest = json.loads(climate_manifest.read_text(encoding="utf-8"))
    if manifest.get("schema") != INDEX_SCHEMA:
        raise ValueError("unsupported climate bundle")
    index_path = climate_manifest.resolve().parent / manifest["index"]["path"]
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError("climate index checksum mismatch")
    index = _load_gzip_json(index_path)

    warming_meta = json.loads(warming_manifest.read_text(encoding="utf-8"))
    if warming_meta.get("schema") != SCHEMA:
        raise ValueError("unsupported warming registry")
    warming_path = Path(warming_meta["asset"]["path"])
    if not warming_path.is_absolute():
        warming_path = warming_manifest.resolve().parent / warming_path
    if sha256(warming_path) != warming_meta["asset"]["sha256"]:
        raise ValueError("warming registry checksum mismatch")
    warming = _load_gzip_json(warming_path)
    records = {str(record["id"]): record for record in warming["pairs"]}

    singles = [pair for pair in index["pairs"] if pair.get("kind") != "multi-model"]
    missing = sorted(str(pair["id"]) for pair in singles if str(pair["id"]) not in records)
    if missing:
        raise ValueError(f"warming registry is missing climate pairs: {missing}")
    for pair in singles:
        record = records[str(pair["id"])]
        if pair["source_label"] != record["source_label"] or pair["member_id"] != record["member_id"]:
            raise ValueError(f"warming identity mismatch for {pair['id']}")
        pair["warming"] = record

    for pair in index["pairs"]:
        if pair.get("kind") != "multi-model":
            continue
        model_records = [records[str(pair_id)] for pair_id in pair["model_ids"]]
        changes = np.asarray([record["change_k"] for record in model_records], dtype=float)
        pair["warming"] = {
            "definition": warming["definition"],
            "method": warming["method"],
            "mean_change_k": float(changes.mean()),
            "minimum_change_k": float(changes.min()),
            "maximum_change_k": float(changes.max()),
            "model_count": len(model_records),
            "models": [{"id": record["id"], "change_k": record["change_k"]} for record in model_records],
        }

    assets_dir = climate_manifest.resolve().parent / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    copied = assets_dir / warming_path.name
    temporary = copied.with_suffix(copied.suffix + f".part-{os.getpid()}")
    shutil.copyfile(warming_path, temporary)
    os.replace(temporary, copied)
    index["warming"] = {
        "definition": warming["definition"],
        "method": warming["method"],
        "published_gwl": warming.get("published_gwl"),
        "asset": {"url": f"assets/{copied.name}", "sha256": sha256(copied), "bytes": copied.stat().st_size},
    }
    index["generated_utc"] = utc_now()
    raw = json.dumps(index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    new_index = climate_manifest.resolve().parent / f"climate-index.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
    _atomic_gzip_json(new_index, index)
    manifest.update(
        {
            "generated_utc": index["generated_utc"],
            "index": {"path": new_index.name, "sha256": sha256(new_index), "bytes": new_index.stat().st_size},
            "warming_schema": SCHEMA,
            "warming_pairs": len(records),
        }
    )
    _atomic_json(climate_manifest, manifest)
    return new_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--run-root", type=Path, action="append", required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--badc-root", type=Path, default=DEFAULT_ROOT)
    build.add_argument("--gwl-table", type=Path, default=DEFAULT_GWL_TABLE)
    attach = subparsers.add_parser("attach")
    attach.add_argument("--climate-manifest", type=Path, required=True)
    attach.add_argument("--warming-manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "build":
        result = build_registry(args.run_root, args.output_dir, args.badc_root, args.gwl_table)
    else:
        result = attach_to_climate_bundle(args.climate_manifest, args.warming_manifest)
    print(result)


if __name__ == "__main__":
    main()
