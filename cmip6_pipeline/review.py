#!/usr/bin/env python3
"""Create a scientific-admissibility review for a staged CMIP6 bundle."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from reanalysis_pipeline.common import sha256


SCHEMA = "lps-atlas-cmip6-scientific-review-v1"
TRACK_POLICY = {
    "event_frequency_ratio": (2.0 / 3.0, 1.5),
    "system_days_ratio": (2.0 / 3.0, 1.5),
    "monthly_cycle_correlation_minimum": 0.5,
    "track_density_correlation_minimum": 0.6,
    "track_density_probability_overlap_minimum": 0.5,
}
CLASSIFICATION_POLICY = {
    "depression_or_stronger_frequency_ratio": (0.5, 2.0),
}
REVIEW_METRICS = (
    "systems",
    "depressions_or_stronger",
    "system_days",
    "mean_duration_hours",
    "mean_peak_wind_ms",
    "mean_peak_pressure_deficit_hpa",
    "mean_peak_24h_precipitation_mm",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_gzip(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _load_asset(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = root / record["url"]
    if sha256(path) != record["sha256"]:
        raise ValueError(f"CMIP6 asset checksum does not match: {path}")
    return _load_gzip(path)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _between(value: Any, bounds: tuple[float, float]) -> bool:
    number = _number(value)
    return number is not None and bounds[0] <= number <= bounds[1]


def _at_least(value: Any, threshold: float) -> bool:
    number = _number(value)
    return number is not None and number >= threshold


def _track_checks(screen: dict[str, Any]) -> dict[str, bool]:
    comparisons = screen.get("comparisons") or {}
    spatial = comparisons.get("track_density_shape") or {}
    return {
        "event_frequency": _between(
            comparisons.get("event_frequency_ratio"),
            TRACK_POLICY["event_frequency_ratio"],
        ),
        "system_days": _between(
            comparisons.get("system_days_ratio"),
            TRACK_POLICY["system_days_ratio"],
        ),
        "monthly_cycle": _at_least(
            comparisons.get("monthly_cycle_correlation"),
            TRACK_POLICY["monthly_cycle_correlation_minimum"],
        ),
        "track_density_correlation": _at_least(
            spatial.get("pattern_correlation_nonempty_union"),
            TRACK_POLICY["track_density_correlation_minimum"],
        ),
        "track_density_overlap": _at_least(
            spatial.get("probability_overlap"),
            TRACK_POLICY["track_density_probability_overlap_minimum"],
        ),
    }


def assess_historical_screen(screen: dict[str, Any]) -> dict[str, Any]:
    jjas = (screen.get("seasonal") or {}).get("jjas") or {}
    all_checks = _track_checks(screen)
    jjas_checks = _track_checks(jjas)
    classification = screen.get("classification_screen") or {}
    jjas_classification = (
        (classification.get("seasonal") or {}).get("jjas")
        or jjas.get("classification_screen")
        or {}
    )
    all_class_ratio = (classification.get("comparisons") or {}).get(
        "depression_or_stronger_frequency_ratio"
    )
    jjas_class_ratio = (jjas_classification.get("comparisons") or {}).get(
        "depression_or_stronger_frequency_ratio"
    )
    class_checks = {
        "all_months": _between(
            all_class_ratio,
            CLASSIFICATION_POLICY["depression_or_stronger_frequency_ratio"],
        ),
        "jjas": _between(
            jjas_class_ratio,
            CLASSIFICATION_POLICY["depression_or_stronger_frequency_ratio"],
        ),
    }
    track_eligible = all(all_checks.values()) and all(jjas_checks.values())
    class_eligible = all(class_checks.values())
    if track_eligible and class_eligible:
        disposition = "headline-all-lps-and-classes"
    elif track_eligible:
        disposition = "headline-all-lps-only"
    else:
        disposition = "exploratory-only"
    return {
        "disposition": disposition,
        "all_lps_headline_eligible": track_eligible,
        "absolute_class_headline_eligible": class_eligible,
        "track_checks": {"all_months": all_checks, "jjas": jjas_checks},
        "classification_checks": class_checks,
    }


def _comparisons(screen: dict[str, Any]) -> dict[str, Any]:
    values = screen.get("comparisons") or {}
    spatial = values.get("track_density_shape") or {}
    return {
        "event_frequency_ratio": values.get("event_frequency_ratio"),
        "system_days_ratio": values.get("system_days_ratio"),
        "median_duration_ratio": values.get("median_duration_ratio"),
        "median_peak_wind_ratio": values.get("median_peak_wind_ratio"),
        "monthly_cycle_correlation": values.get("monthly_cycle_correlation"),
        "track_density_pattern_correlation": spatial.get(
            "pattern_correlation_nonempty_union"
        ),
        "track_density_probability_overlap": spatial.get("probability_overlap"),
        "depression_or_stronger_frequency_ratio": values.get(
            "depression_or_stronger_frequency_ratio"
        ),
        "deep_depression_or_stronger_frequency_ratio": values.get(
            "deep_depression_or_stronger_frequency_ratio"
        ),
    }


def _changes(payload: dict[str, Any], season: str) -> dict[str, Any]:
    fields = (
        "historical",
        "future",
        "absolute_change",
        "percent_change",
        "ci05",
        "ci95",
    )
    return {
        metric: {
            key: (payload["seasonal_changes"][season].get(metric) or {}).get(key)
            for key in fields
        }
        for metric in REVIEW_METRICS
    }


def _model_record(root: Path, pair: dict[str, Any]) -> dict[str, Any]:
    historical = _load_asset(root, pair["historical"])
    future = _load_asset(root, pair["future"])
    change = _load_asset(root, pair["change"])
    screen = (historical.get("qa") or {}).get("historical_screen") or {}
    jjas = (screen.get("seasonal") or {}).get("jjas") or {}
    return {
        "id": pair["id"],
        "source_label": pair["source_label"],
        "member_id": pair["member_id"],
        "historical_coverage": historical.get("coverage"),
        "future_coverage": future.get("coverage"),
        "comparisons": {
            "all_months": _comparisons(screen),
            "jjas": _comparisons(jjas),
        },
        "assessment": assess_historical_screen(screen),
        "changes": {
            "all_months": _changes(change, "all"),
            "jjas": _changes(change, "jjas"),
        },
    }


def _signal_summary(models: list[dict[str, Any]], season: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for metric in REVIEW_METRICS:
        values = [model["changes"][season][metric] for model in models]
        absolute = [_number(value.get("absolute_change")) for value in values]
        percentages = [_number(value.get("percent_change")) for value in values]
        clean_percentages = [value for value in percentages if value is not None]
        positive = sum(value is not None and value > 0 for value in absolute)
        negative = sum(value is not None and value < 0 for value in absolute)
        result[metric] = {
            "models": len(values),
            "positive": positive,
            "negative": negative,
            "zero": len(values) - positive - negative,
            "individual_ci_excludes_zero_positive": sum(
                _number(value.get("ci05")) is not None and float(value["ci05"]) > 0
                for value in values
            ),
            "individual_ci_excludes_zero_negative": sum(
                _number(value.get("ci95")) is not None and float(value["ci95"]) < 0
                for value in values
            ),
            "median_model_percent_change": (
                float(np.median(clean_percentages)) if clean_percentages else None
            ),
        }
    return result


def build_review(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.resolve().parent
    index_path = root / manifest["index"]["path"]
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError("CMIP6 index checksum does not match its manifest")
    index = _load_gzip(index_path)
    pairs = [pair for pair in index["pairs"] if pair.get("kind") != "multi-model"]
    models = [_model_record(root, pair) for pair in pairs]
    total_eligible = [
        model["source_label"]
        for model in models
        if model["assessment"]["all_lps_headline_eligible"]
    ]
    class_eligible = [
        model["source_label"]
        for model in models
        if model["assessment"]["absolute_class_headline_eligible"]
    ]
    signals = {
        "all_months": _signal_summary(models, "all_months"),
        "jjas": _signal_summary(models, "jjas"),
    }
    return _review_payload(
        manifest_path,
        manifest,
        index,
        models,
        total_eligible,
        class_eligible,
        signals,
    )


def _format(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}f}"


def markdown(review: dict[str, Any]) -> str:
    realism_rows: list[str] = []
    change_rows: list[str] = []
    for model in review["models"]:
        comparison = model["comparisons"]["jjas"]
        realism_rows.append(
            "| "
            + " | ".join(
                (
                    model["source_label"],
                    _format(comparison["event_frequency_ratio"]),
                    _format(comparison["system_days_ratio"]),
                    _format(comparison["monthly_cycle_correlation"]),
                    _format(comparison["track_density_pattern_correlation"]),
                    _format(comparison["track_density_probability_overlap"]),
                    _format(comparison["depression_or_stronger_frequency_ratio"]),
                    model["assessment"]["disposition"],
                )
            )
            + " |"
        )
        change = model["changes"]["jjas"]
        change_rows.append(
            "| "
            + " | ".join(
                (
                    model["source_label"],
                    _format(change["systems"]["percent_change"], 1),
                    _format(change["system_days"]["percent_change"], 1),
                    _format(change["mean_peak_wind_ms"]["percent_change"], 1),
                    _format(
                        change["mean_peak_pressure_deficit_hpa"]["percent_change"],
                        1,
                    ),
                    _format(
                        change["mean_peak_24h_precipitation_mm"]["percent_change"],
                        1,
                    ),
                )
            )
            + " |"
        )
    decision = review["decision"]
    rain_signal = review["signal_agreement"]["jjas"][
        "mean_peak_24h_precipitation_mm"
    ]
    lines = [
        "# CMIP6 LPS candidate review",
        "",
        f"Decision: **{decision['status']}**. {decision['reason']}",
        "",
        "## Historical JJAS realism relative to ERA5 v5.6",
        "",
        "| Model | systems | system-days | monthly r | density r | density overlap | D+ rate | disposition |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
        *realism_rows,
        "",
        "Ratios are model/reference. Density r excludes cells empty in both sources.",
        "",
        "## SSP2-4.5 late-century JJAS changes",
        "",
        "| Model | systems % | system-days % | peak wind % | pressure deficit % | peak 24 h rain % |",
        "|---|---:|---:|---:|---:|---:|",
        *change_rows,
        "",
        (
            "Track-centred 24-hour precipitation increases in "
            f"{rain_signal['positive']} of {rain_signal['models']} models. "
            "These are staging diagnostics, not a public claim."
        ),
        "",
    ]
    return "\n".join(lines)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    review = build_review(args.manifest)
    json_path = args.output_dir / "cmip6-scientific-review.json"
    markdown_path = args.output_dir / "cmip6-scientific-review.md"
    _atomic_text(json_path, json.dumps(review, indent=2, sort_keys=True) + "\n")
    _atomic_text(markdown_path, markdown(review))
    print(json_path)
    print(markdown_path)


def _review_payload(
    manifest_path: Path,
    manifest: dict[str, Any],
    index: dict[str, Any],
    models: list[dict[str, Any]],
    total_eligible: list[str],
    class_eligible: list[str],
    signals: dict[str, Any],
) -> dict[str, Any]:
    ready = len(total_eligible) >= 2
    classes_ready = len(class_eligible) >= 2
    findings = [
        (
            {
                "severity": "medium",
                "confidence": "high",
                "finding": (
                    f"The {len(models)}-model candidate meets the automated minimum "
                    "historical screen for an explicit human review."
                ),
                "impact": (
                    f"{len(total_eligible)} independently tracked models support an "
                    "all-LPS headline; failing models must remain visibly identified."
                ),
                "remediation": (
                    "Inspect the exact model dispositions and spatial diagnostics before "
                    "issuing an approval record."
                ),
            }
            if ready
            else {
                "severity": "high",
                "confidence": "high",
                "finding": (
                    f"The {len(models)}-model candidate is not yet suitable for a headline "
                    "multi-model projection."
                ),
                "impact": (
                    "Historically under-detecting models would dominate an equal-weight mean."
                ),
                "remediation": (
                    "Add another independently credible model or publish only an explicitly "
                    "single-model result."
                ),
            }
        ),
        (
            {
                "severity": "medium",
                "confidence": "medium",
                "finding": (
                    "At least two models pass the absolute D/DD/CS historical-frequency "
                    "screen, but threshold classes remain resolution-sensitive."
                ),
                "impact": "Class-filtered changes still need explicit scientific review.",
                "remediation": (
                    "Compare the eligible models and continuous intensity diagnostics before "
                    "using class-filtered changes as a headline."
                ),
            }
            if classes_ready
            else {
                "severity": "high",
                "confidence": "high",
                "finding": "Absolute D/DD/CS category frequencies are not publication-ready.",
                "impact": (
                    "Class-filtered changes would mix climate response with threshold and grid bias."
                ),
                "remediation": (
                    "Restrict any first view to all LPSs and continuous intensity diagnostics."
                ),
            }
        ),
        {
            "severity": "medium",
            "confidence": "medium",
            "finding": (
                "The JJAS track-centred precipitation response is the most consistent "
                "preliminary intensity signal."
            ),
            "evidence": signals["jjas"]["mean_peak_24h_precipitation_mm"],
            "remediation": "Reassess after expanding the admissible model set.",
        },
    ]
    return {
        "schema": SCHEMA,
        "generated_utc": utc_now(),
        "candidate": {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": sha256(manifest_path),
            "index_sha256": manifest["index"]["sha256"],
            "status": index.get("status"),
            "models": len(models),
        },
        "review_policy": {
            "all_lps_track_climatology": TRACK_POLICY,
            "absolute_intensity_classification": CLASSIFICATION_POLICY,
            "minimum_models_for_headline_ensemble": 2,
            "note": "Conservative publication rubric; no detector thresholds are retuned.",
        },
        "decision": {
            "status": "ready-for-explicit-human-approval" if ready else "hold-publication",
            "all_lps_headline_eligible_models": total_eligible,
            "absolute_class_headline_eligible_models": class_eligible,
            "reason": (
                (
                    f"{len(total_eligible)} independently tracked models pass the conservative "
                    "all-LPS historical screen, meeting the automated minimum; explicit human "
                    "approval is still required."
                )
                if ready
                else (
                    f"Only {len(total_eligible)} independently tracked model"
                    f"{'s' if len(total_eligible) != 1 else ''} "
                    f"{'pass' if len(total_eligible) != 1 else 'passes'} the conservative all-LPS "
                    "historical screen; at least two are required for a multi-model headline."
                )
            ),
        },
        "models": models,
        "signal_agreement": signals,
        "findings": findings,
        "assumptions": [
            "ERA5 v5.6 over the identical 1981–2010 years is the reference.",
            "Unique-track density on the common 1-degree grid is the spatial diagnostic.",
            "A bootstrap interval excluding zero is insufficient without historical realism.",
        ],
    }


if __name__ == "__main__":
    main()
