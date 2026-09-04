#!/usr/bin/env python3
"""Validate a final CMIP6 LPS catalogue and screen its historical realism."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from reanalysis_pipeline.common import sha256

from .summarise import density, event_summary


SCHEMA = "lps-atlas-cmip6-catalogue-qa-v1"
DEFAULT_REFERENCE = (
    Path(__file__).resolve().parents[2]
    / "lps-v5.3-continuity-framework/production/v5.6/public-release/"
    "lps_v5.6-era5-1940-2025-core.parquet"
)
REQUIRED_COLUMNS = {
    "track_id",
    "time",
    "lon",
    "lat",
    "position_source",
    "max_vort_smoothed",
    "precip_24hr",
    "pressure_deficit_hpa",
    "p95_anomaly_wind_125km_ms",
    "physics_complete",
    "physics_gap_supported",
    "v55_event_existence_gate",
    "event_peak_imd_category",
    "event_peak_imd_label",
    "intensity_method",
}
FINITE_COLUMNS = (
    "lon",
    "lat",
    "precip_24hr",
    "pressure_deficit_hpa",
    "p95_anomaly_wind_125km_ms",
    "event_peak_imd_category",
)
SEASONS = {
    "all": tuple(range(1, 13)),
    "jjas": (6, 7, 8, 9),
}
MAXIMUM_HOURLY_STEP_KM = 150.0
MINIMUM_EVENT_HOURS = 72


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _quantiles(values: pd.Series) -> dict[str, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {key: None for key in ("minimum", "q25", "median", "q75", "maximum")}
    result = clean.quantile([0.0, 0.25, 0.5, 0.75, 1.0]).to_numpy(float)
    return {
        key: float(value)
        for key, value in zip(("minimum", "q25", "median", "q75", "maximum"), result, strict=True)
    }


def _haversine_steps(frame: pd.DataFrame) -> np.ndarray:
    ordered = frame.sort_values(["track_id", "time"], kind="mergesort")
    lon = np.deg2rad(pd.to_numeric(ordered.lon, errors="coerce").to_numpy(float))
    lat = np.deg2rad(pd.to_numeric(ordered.lat, errors="coerce").to_numpy(float))
    same_track = ordered.track_id.to_numpy()[1:] == ordered.track_id.to_numpy()[:-1]
    value = (
        np.sin(np.diff(lat) / 2.0) ** 2
        + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2.0) ** 2
    )
    steps = 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))
    return steps[same_track]


def _core_bounds(plan: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp, list[int], int]:
    start = pd.Period(str(plan["core_start"]), freq="M").start_time
    end = (pd.Period(str(plan["core_end"]), freq="M") + 1).start_time - pd.Timedelta(hours=1)
    periods = pd.period_range(start=start, end=end, freq="M")
    months = sorted(set(int(period.month) for period in periods))
    years = len(sorted(set(int(period.year) for period in periods)))
    return start, end, months, years


def _catalogue_metrics(frame: pd.DataFrame, months: list[int], years: int) -> dict[str, Any]:
    events = event_summary(frame)
    selected = events.loc[events.genesis_month.isin(months)].copy()
    selected_ids = set(selected.track_id)
    positions = frame.loc[frame.track_id.isin(selected_ids)]
    month_counts = selected.genesis_month.value_counts().reindex(range(1, 13), fill_value=0).sort_index()
    class_counts = selected.peak_category.value_counts().sort_index()
    return {
        "events": int(len(selected)),
        "positions": int(len(positions)),
        "events_per_year": float(len(selected) / years),
        "system_days_per_year": float(selected.duration_hours.sum() / 24.0 / years),
        "duration_hours": _quantiles(selected.duration_hours),
        "path_length_km": _quantiles(selected.path_length_km),
        "peak_wind_ms": _quantiles(selected.peak_wind_ms),
        "peak_pressure_deficit_hpa": _quantiles(selected.peak_pressure_deficit_hpa),
        "peak_24h_precipitation_mm": _quantiles(selected.peak_24h_precipitation_mm),
        "class_counts": {str(int(key)): int(value) for key, value in class_counts.items()},
        "depressions_or_stronger_per_year": float(selected.peak_category.ge(2).sum() / years),
        "deep_depressions_or_stronger_per_year": float(selected.peak_category.ge(3).sum() / years),
        "cyclonic_storms_or_stronger_per_year": float(selected.peak_category.ge(4).sum() / years),
        "depression_or_stronger_fraction": (
            float(selected.peak_category.ge(2).mean()) if len(selected) else None
        ),
        "monthly_genesis_counts": [int(value) for value in month_counts.to_numpy()],
    }


def _positions_for_genesis_months(frame: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    genesis = frame.groupby("track_id", sort=False).time.min()
    selected = genesis.loc[genesis.dt.month.isin(months)].index
    return frame.loc[frame.track_id.isin(selected)]


def _track_density_comparison(
    model: pd.DataFrame,
    reference: pd.DataFrame,
    months: list[int],
) -> dict[str, float | int | None]:
    """Compare unique-track density shape without allowing empty cells to inflate r."""

    model_positions = _positions_for_genesis_months(model, months)
    reference_positions = _positions_for_genesis_months(reference, months)
    model_counts = np.asarray(density(model_positions)["unique_track_counts"], dtype=float)
    reference_counts = np.asarray(
        density(reference_positions)["unique_track_counts"], dtype=float
    )
    model_occupied = model_counts > 0
    reference_occupied = reference_counts > 0
    occupied_union = model_occupied | reference_occupied
    occupied_intersection = model_occupied & reference_occupied
    model_total = float(model_counts.sum())
    reference_total = float(reference_counts.sum())
    dot = float(np.dot(model_counts.ravel(), reference_counts.ravel()))
    norm = float(
        np.sqrt(
            np.dot(model_counts.ravel(), model_counts.ravel())
            * np.dot(reference_counts.ravel(), reference_counts.ravel())
        )
    )
    correlation: float | None = None
    if int(occupied_union.sum()) >= 3:
        model_union = model_counts[occupied_union]
        reference_union = reference_counts[occupied_union]
        if np.std(model_union) > 0 and np.std(reference_union) > 0:
            correlation = float(np.corrcoef(model_union, reference_union)[0, 1])
    probability_overlap: float | None = None
    if model_total > 0 and reference_total > 0:
        probability_overlap = float(
            np.minimum(model_counts / model_total, reference_counts / reference_total).sum()
        )
    return {
        "pattern_correlation_nonempty_union": correlation,
        "cosine_similarity": float(dot / norm) if norm > 0 else None,
        "probability_overlap": probability_overlap,
        "occupied_cell_jaccard": (
            float(occupied_intersection.sum() / occupied_union.sum())
            if occupied_union.any()
            else None
        ),
        "model_occupied_cells": int(model_occupied.sum()),
        "reference_occupied_cells": int(reference_occupied.sum()),
    }


def _select_reference(
    reference: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    months: list[int],
) -> pd.DataFrame:
    reference = reference.copy()
    reference["time"] = pd.to_datetime(reference.time, errors="raise")
    genesis = reference.groupby("track_id", sort=False).time.min()
    selected = genesis.loc[
        genesis.dt.year.between(start.year, end.year) & genesis.dt.month.isin(months)
    ].index
    return reference.loc[
        reference.track_id.isin(selected) & reference.time.between(start, end)
    ].copy()


def _safe_ratio(model: float | None, reference: float | None) -> float | None:
    if model is None or reference is None or not np.isfinite(reference) or reference == 0:
        return None
    return float(model / reference)


def _safe_correlation(left: list[int], right: list[int], months: list[int]) -> float | None:
    indexes = np.asarray(months, dtype=int) - 1
    x = np.asarray(left, dtype=float)[indexes]
    y = np.asarray(right, dtype=float)[indexes]
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def historical_screen(
    model: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    months: list[int],
    years: int,
) -> dict[str, Any]:
    model_metrics = _catalogue_metrics(model, months, years)
    selected_reference = _select_reference(reference, start, end, months)
    reference_metrics = _catalogue_metrics(selected_reference, months, years)
    comparisons = {
        "event_frequency_ratio": _safe_ratio(
            model_metrics["events_per_year"], reference_metrics["events_per_year"]
        ),
        "system_days_ratio": _safe_ratio(
            model_metrics["system_days_per_year"], reference_metrics["system_days_per_year"]
        ),
        "median_duration_ratio": _safe_ratio(
            model_metrics["duration_hours"]["median"], reference_metrics["duration_hours"]["median"]
        ),
        "median_path_length_ratio": _safe_ratio(
            model_metrics["path_length_km"]["median"], reference_metrics["path_length_km"]["median"]
        ),
        "median_peak_wind_ratio": _safe_ratio(
            model_metrics["peak_wind_ms"]["median"], reference_metrics["peak_wind_ms"]["median"]
        ),
        "median_peak_pressure_deficit_ratio": _safe_ratio(
            model_metrics["peak_pressure_deficit_hpa"]["median"],
            reference_metrics["peak_pressure_deficit_hpa"]["median"],
        ),
        "monthly_cycle_correlation": _safe_correlation(
            model_metrics["monthly_genesis_counts"], reference_metrics["monthly_genesis_counts"], months
        ),
        "depression_or_stronger_frequency_ratio": _safe_ratio(
            model_metrics["depressions_or_stronger_per_year"],
            reference_metrics["depressions_or_stronger_per_year"],
        ),
        "deep_depression_or_stronger_frequency_ratio": _safe_ratio(
            model_metrics["deep_depressions_or_stronger_per_year"],
            reference_metrics["deep_depressions_or_stronger_per_year"],
        ),
        "cyclonic_storm_or_stronger_frequency_ratio": _safe_ratio(
            model_metrics["cyclonic_storms_or_stronger_per_year"],
            reference_metrics["cyclonic_storms_or_stronger_per_year"],
        ),
        "track_density_shape": _track_density_comparison(
            model, selected_reference, months
        ),
    }
    frequency = comparisons["event_frequency_ratio"]
    duration = comparisons["median_duration_ratio"]
    wind = comparisons["median_peak_wind_ratio"]
    diagnostic_flags = {
        "event_frequency_within_factor_two": frequency is not None and 0.5 <= frequency <= 2.0,
        "median_duration_within_factor_two": duration is not None and 0.5 <= duration <= 2.0,
        "median_peak_wind_within_factor_two": wind is not None and 0.5 <= wind <= 2.0,
    }
    depression_ratio = comparisons["depression_or_stronger_frequency_ratio"]
    classification_flags = {
        "depression_or_stronger_frequency_within_factor_two": (
            depression_ratio is not None and 0.5 <= depression_ratio <= 2.0
        )
    }
    return {
        "reference": "ERA5-derived LPS v5.6",
        "reference_catalogue_sha256": None,
        "coverage": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "months": months,
            "years": years,
        },
        "model": model_metrics,
        "reference_metrics": reference_metrics,
        "comparisons": comparisons,
        "diagnostic_flags": diagnostic_flags,
        "classification_screen": {
            "comparisons": {
                key: comparisons[key]
                for key in (
                    "depression_or_stronger_frequency_ratio",
                    "deep_depression_or_stronger_frequency_ratio",
                    "cyclonic_storm_or_stronger_frequency_ratio",
                )
            },
            "diagnostic_flags": classification_flags,
            "screening_status": (
                "engineering-sample-only"
                if years < 10
                else (
                    "passes-basic-classification-screen"
                    if all(classification_flags.values())
                    else "review-classification-bias"
                )
            ),
            "interpretation": (
                "Absolute v5.5.1 category-frequency screen. It is reported separately from "
                "all-system track realism because threshold classes are resolution-sensitive."
            ),
        },
        "screening_status": (
            "engineering-sample-only"
            if years < 10
            else ("passes-basic-historical-screen" if all(diagnostic_flags.values()) else "review-model-bias")
        ),
        "interpretation": (
            "Historical-performance screen only. Flags diagnose model and resolution bias; "
            "they do not retune the detector or intensity thresholds. Track-density shape uses "
            "unique tracks per 1-degree cell; its correlation excludes cells empty in both sources."
        ),
    }


def validate_catalogue(
    catalogue: Path,
    period_plan: Path,
    *,
    reference: Path | None = DEFAULT_REFERENCE,
) -> dict[str, Any]:
    plan = json.loads(period_plan.read_text(encoding="utf-8"))
    start, end, months, years = _core_bounds(plan)
    frame = pd.read_parquet(catalogue)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    failures: list[str] = []
    if missing:
        failures.append(f"missing required columns: {', '.join(missing)}")
        return {
            "schema": SCHEMA,
            "status": "failed",
            "generated_utc": utc_now(),
            "catalogue": {"path": str(catalogue.resolve()), "sha256": sha256(catalogue)},
            "period_plan": {"path": str(period_plan.resolve()), "sha256": sha256(period_plan)},
            "failures": failures,
        }
    if frame.empty:
        failures.append("catalogue is empty")
        return {
            "schema": SCHEMA,
            "status": "failed",
            "generated_utc": utc_now(),
            "catalogue": {
                "path": str(catalogue.resolve()),
                "sha256": sha256(catalogue),
                "rows": 0,
                "events": 0,
            },
            "period_plan": {"path": str(period_plan.resolve()), "sha256": sha256(period_plan)},
            "run": plan.get("run", {}),
            "failures": failures,
        }
    frame["time"] = pd.to_datetime(frame.time, errors="raise")
    frame.sort_values(["track_id", "time"], kind="mergesort", inplace=True)
    duplicate_track_times = int(frame.duplicated(["track_id", "time"]).sum())
    if duplicate_track_times:
        failures.append(f"{duplicate_track_times} duplicate track/time rows")
    if "row_id" in frame and frame.row_id.duplicated().any():
        failures.append("row_id is not unique")
    for column in FINITE_COLUMNS:
        missing_values = int((~np.isfinite(pd.to_numeric(frame[column], errors="coerce"))).sum())
        if missing_values:
            failures.append(f"{column} has {missing_values} non-finite rows")
    if not frame.lon.between(45.0, 120.0).all() or not frame.lat.between(-15.0, 45.0).all():
        failures.append("published centres leave the common tracking domain")
    if frame.time.min() < start or frame.time.max() > end:
        failures.append("catalogue timestamps leave the planned core interval")
    elapsed = frame.groupby("track_id", sort=False).time.diff().dt.total_seconds().div(3600.0)
    non_hourly_steps = int(elapsed.dropna().ne(1.0).sum())
    if non_hourly_steps:
        failures.append(f"{non_hourly_steps} within-track steps are not one hour")
    event_sizes = frame.groupby("track_id", sort=False).size()
    short_events = int(event_sizes.lt(MINIMUM_EVENT_HOURS).sum())
    if short_events:
        failures.append(f"{short_events} events are shorter than {MINIMUM_EVENT_HOURS} hours")
    if not frame.physics_gap_supported.astype(bool).all():
        failures.append("final catalogue contains physics-gap-unsupported rows")
    if not frame.v55_event_existence_gate.astype(str).eq("calibrated_physical_support_v1").all():
        failures.append("final catalogue contains rows outside the frozen physical-event gate")
    peak_variants = frame.groupby("track_id", sort=False).event_peak_imd_category.nunique(dropna=False)
    if peak_variants.gt(1).any():
        failures.append("event peak category is not constant within every track")
    steps = _haversine_steps(frame)
    maximum_step = float(np.nanmax(steps)) if len(steps) else 0.0
    if maximum_step > MAXIMUM_HOURLY_STEP_KM:
        failures.append(
            f"maximum hourly centre displacement is {maximum_step:.1f} km, above {MAXIMUM_HOURLY_STEP_KM:.0f} km"
        )
    finite_vorticity_by_track = frame.groupby("track_id", sort=False).max_vort_smoothed.apply(
        lambda values: np.isfinite(pd.to_numeric(values, errors="coerce")).any()
    )
    if not finite_vorticity_by_track.all():
        failures.append("at least one event has no finite final-centre smoothed vorticity")

    events = event_summary(frame)
    boundary_censored = int((events.start.le(start) | events.end.ge(end)).sum())
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "passed" if not failures else "failed",
        "generated_utc": utc_now(),
        "catalogue": {
            "path": str(catalogue.resolve()),
            "sha256": sha256(catalogue),
            "rows": int(len(frame)),
            "events": int(frame.track_id.nunique()),
        },
        "period_plan": {"path": str(period_plan.resolve()), "sha256": sha256(period_plan)},
        "run": plan.get("run", {}),
        "core": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "months": months,
            "years": years,
        },
        "checks": {
            "duplicate_track_times": duplicate_track_times,
            "non_hourly_steps": non_hourly_steps,
            "short_events": short_events,
            "maximum_hourly_step_km": maximum_step,
            "maximum_allowed_hourly_step_km": MAXIMUM_HOURLY_STEP_KM,
            "physics_complete_fraction": float(frame.physics_complete.astype(bool).mean()),
            "boundary_censored_events": boundary_censored,
        },
        "diagnostics": _catalogue_metrics(frame, months, years),
        "failures": failures,
    }
    if plan.get("run", {}).get("experiment_id") == "historical" and reference is not None:
        if not reference.is_file():
            failures.append(f"historical reference is absent: {reference}")
            record["status"] = "failed"
        else:
            reference_frame = pd.read_parquet(reference)
            screen = historical_screen(
                frame,
                reference_frame,
                start=start,
                end=end,
                months=months,
                years=years,
            )
            reference_sha256 = sha256(reference)
            screen["reference_catalogue_sha256"] = reference_sha256
            jjas_screen = historical_screen(
                frame,
                reference_frame,
                start=start,
                end=end,
                months=[6, 7, 8, 9],
                years=years,
            )
            jjas_screen["reference_catalogue_sha256"] = reference_sha256
            all_month_status = screen["screening_status"]
            screen["seasonal"] = {"jjas": jjas_screen}
            screen["screening_components"] = {
                "all_months": all_month_status,
                "jjas": jjas_screen["screening_status"],
            }
            classification = screen["classification_screen"]
            jjas_classification = jjas_screen["classification_screen"]
            all_month_classification_status = classification["screening_status"]
            classification["seasonal"] = {"jjas": jjas_classification}
            classification["screening_components"] = {
                "all_months": all_month_classification_status,
                "jjas": jjas_classification["screening_status"],
            }
            if years >= 10:
                screen["screening_status"] = (
                    "passes-basic-historical-screen"
                    if all(
                        status == "passes-basic-historical-screen"
                        for status in screen["screening_components"].values()
                    )
                    else "review-model-bias"
                )
                classification["screening_status"] = (
                    "passes-basic-classification-screen"
                    if all(
                        status == "passes-basic-classification-screen"
                        for status in classification["screening_components"].values()
                    )
                    else "review-classification-bias"
                )
            screen["interpretation"] = (
                "Historical-performance screens for both all months and JJAS. Track realism and "
                "absolute intensity-class realism are reported separately; neither screen retunes "
                "the detector or intensity thresholds."
            )
            record["historical_screen"] = screen
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--period-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = validate_catalogue(args.catalogue, args.period_plan, reference=args.reference)
    atomic_json(args.output, result)
    print(args.output)
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
