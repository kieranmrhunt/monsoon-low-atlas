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
INDEX_SCHEMA = "lps-atlas-cmip6-climate-index-v1"
LAT_EDGES = np.arange(-15.0, 46.0, 1.0)
LON_EDGES = np.arange(45.0, 121.0, 1.0)
SEASONS = {
    "all": tuple(range(1, 13)),
    "jjas": (6, 7, 8, 9),
    "mam": (3, 4, 5),
    "ond": (10, 11, 12),
    "djf": (12, 1, 2),
}
CHANGE_METRICS = (
    "systems",
    "depressions_or_stronger",
    "deep_depressions_or_stronger",
    "cyclonic_storms_or_stronger",
    "system_days",
    "mean_duration_hours",
    "mean_peak_wind_ms",
    "mean_peak_pressure_deficit_hpa",
    "mean_peak_24h_precipitation_mm",
)
ANNUAL_COLUMNS = ("year", *CHANGE_METRICS)
EVENT_COLUMNS = (
    "track_id",
    "start",
    "end",
    "genesis_year",
    "genesis_month",
    "duration_hours",
    "path_length_km",
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


def event_summary(frame: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for track_id, group in frame.groupby("track_id", sort=True):
        group = group.sort_values("time")
        lon = pd.to_numeric(group.lon, errors="coerce").to_numpy(float)
        lat = pd.to_numeric(group.lat, errors="coerce").to_numpy(float)
        records.append(
            {
                "track_id": int(track_id),
                "start": group.time.iloc[0],
                "end": group.time.iloc[-1],
                "genesis_year": int(group.time.iloc[0].year),
                "genesis_month": int(group.time.iloc[0].month),
                "duration_hours": int(len(group)),
                "path_length_km": float(np.nansum(haversine_steps(lon, lat))),
                "genesis_lon": float(lon[0]),
                "genesis_lat": float(lat[0]),
                "lysis_lon": float(lon[-1]),
                "lysis_lat": float(lat[-1]),
                "peak_category": int(pd.to_numeric(group.event_peak_imd_category, errors="coerce").max()),
                "peak_wind_ms": float(pd.to_numeric(group.p95_anomaly_wind_125km_ms, errors="coerce").max()),
                "peak_pressure_deficit_hpa": float(pd.to_numeric(group.pressure_deficit_hpa, errors="coerce").max()),
                "peak_vorticity_x1e5_s1": float(pd.to_numeric(group.max_vort_smoothed, errors="coerce").max()),
                "peak_24h_precipitation_mm": float(pd.to_numeric(group.precip_24hr, errors="coerce").max()),
                "mean_24h_precipitation_mm": float(pd.to_numeric(group.precip_24hr, errors="coerce").mean()),
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
        rows.append(
            {
                "year": int(year),
                "systems": int(len(group)),
                "depressions_or_stronger": int(group.peak_category.ge(2).sum()),
                "deep_depressions_or_stronger": int(group.peak_category.ge(3).sum()),
                "cyclonic_storms_or_stronger": int(group.peak_category.ge(4).sum()),
                "system_days": float(group.duration_hours.sum() / 24.0),
                "mean_duration_hours": float(group.duration_hours.mean()),
                "mean_peak_wind_ms": float(group.peak_wind_ms.mean()),
                "mean_peak_pressure_deficit_hpa": float(group.peak_pressure_deficit_hpa.mean()),
                "mean_peak_24h_precipitation_mm": float(group.peak_24h_precipitation_mm.mean()),
            }
        )
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
) -> Path:
    frame = pd.read_parquet(catalogue)
    frame["time"] = pd.to_datetime(frame.time, errors="raise")
    if frame.empty:
        raise ValueError("cannot summarise an empty CMIP6 catalogue")
    events = event_summary(frame)
    observed_start_year = int(frame.time.min().year)
    observed_end_year = int(frame.time.max().year)
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
        "season_definitions": {key: list(months) for key, months in SEASONS.items()},
        "seasonal": seasonal_summary(frame, events, start_year, end_year),
        "monthly": monthly_summary(events, years),
        "provenance": {
            "catalogue_filename": catalogue.name,
            "catalogue_sha256": sha256(catalogue),
            "intensity_method": sorted(frame.intensity_method.astype(str).unique().tolist()),
        },
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
        return {"historical": None, "future": None, "absolute_change": None, "percent_change": None, "ci05": None, "ci95": None}
    rng = np.random.default_rng(seed)
    differences = (
        right[rng.integers(0, len(right), size=(samples, len(right)))].mean(axis=1)
        - left[rng.integers(0, len(left), size=(samples, len(left)))].mean(axis=1)
    )
    historical_mean = float(left.mean())
    future_mean = float(right.mean())
    return {
        "historical": historical_mean,
        "future": future_mean,
        "absolute_change": future_mean - historical_mean,
        "percent_change": ((future_mean / historical_mean) - 1.0) * 100.0 if historical_mean != 0 else None,
        "ci05": float(np.quantile(differences, 0.05)),
        "ci95": float(np.quantile(differences, 0.95)),
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
        "source_label": historical_run["source_label"],
        "member_id": historical_run["member_id"],
        "historical": {
            "run": historical_run,
            "coverage": historical_payload["coverage"],
            **copied["historical"],
        },
        "future": {
            "run": future_run,
            "coverage": future_payload["coverage"],
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
        "status": "validated-production-window" if production_ready else "engineering-canary",
        "pairs": pairs,
        "defaults": {"pair": pair_id, "season": "jjas", "metric": "systems"},
        "interpretation": (
            "Each entry is a single-model paired change; multi-model projection statements "
            "require an ensemble of entries."
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
    planned = subparsers.add_parser("run-from-plan")
    planned.add_argument("--catalogue", type=Path, required=True)
    planned.add_argument("--period-plan", type=Path, required=True)
    planned.add_argument("--output-dir", type=Path, required=True)
    pair = subparsers.add_parser("pair")
    pair.add_argument("--historical-manifest", type=Path, required=True)
    pair.add_argument("--future-manifest", type=Path, required=True)
    pair.add_argument("--output-dir", type=Path, required=True)
    publish = subparsers.add_parser("publish-pair")
    publish.add_argument("--historical-manifest", type=Path, required=True)
    publish.add_argument("--future-manifest", type=Path, required=True)
    publish.add_argument("--pair-manifest", type=Path, required=True)
    publish.add_argument("--output-dir", type=Path, required=True)
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
        )
    elif args.command == "pair":
        path = summarise_pair(args.historical_manifest, args.future_manifest, args.output_dir)
    else:
        path = publish_pair(
            args.historical_manifest,
            args.future_manifest,
            args.pair_manifest,
            args.output_dir,
        )
    print(path)


if __name__ == "__main__":
    main()
