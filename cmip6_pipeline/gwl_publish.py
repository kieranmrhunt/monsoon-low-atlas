#!/usr/bin/env python3
"""Attach completed global-warming-level LPS comparisons to the atlas bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from reanalysis_pipeline.common import sha256

from .summarise import (
    INDEX_SCHEMA,
    SCHEMA,
    _browser_asset,
    _manifest_asset,
    aggregate_change_payloads,
    aggregate_run_payloads,
    atomic_gzip_json,
    atomic_json,
    summarise_pair,
    utc_now,
)


GWL_SCHEMA = "lps-atlas-cmip6-gwl-browser-v1"


def _load_gzip(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _load_summary(manifest: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata, asset = _manifest_asset(manifest)
    payload = _load_gzip(asset)
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported run summary: {manifest}")
    return metadata, payload


def _identity(payload: dict[str, Any]) -> tuple[str, str]:
    run = payload["run"]
    return str(run["source_label"]), str(run["member_id"])


def _gwl_level(payload: dict[str, Any]) -> float:
    label = str(payload["run"]["period_label"])
    # The level itself is not encoded in old summary files, so callers attach
    # it from the CLI. This guard prevents accidentally treating a fixed
    # late-century window as a GWL run.
    if "–" not in label and "-" not in label:
        raise ValueError(f"GWL run has an unexpected period label: {label}")
    return float("nan")


def _pair_id(source: str, member: str, scenario: str, level: float) -> str:
    identity = f"gwl|1981-2010|{scenario}|{level:g}|{source}|{member}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def attach_gwl_comparisons(
    climate_manifest: Path,
    historical_manifests: list[Path],
    future_specs: list[tuple[float, Path]],
    *,
    scenario: str = "ssp245",
    source_url: str = "https://github.com/IPCC-WG1/Atlas/tree/main/warming-levels",
) -> Path:
    """Publish single-model and equal-model GWL comparisons into one bundle.

    A fixed 1981--2010 model baseline is paired with each model's published
    centred 20-year GWL window. Models without a completed window are omitted
    from that comparison and the resulting per-metric model count stays
    explicit in the browser payload.
    """

    climate_manifest = climate_manifest.resolve()
    output_root = climate_manifest.parent
    manifest = json.loads(climate_manifest.read_text(encoding="utf-8"))
    if manifest.get("schema") != INDEX_SCHEMA:
        raise ValueError("climate manifest has an unsupported schema")
    index_path = output_root / manifest["index"]["path"]
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError("climate index checksum mismatch")
    index = _load_gzip(index_path)
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError("climate index has an unsupported schema")

    historical: dict[tuple[str, str], tuple[Path, dict[str, Any], dict[str, Any]]] = {}
    for path in historical_manifests:
        metadata, payload = _load_summary(path.resolve())
        if payload["run"]["experiment_id"] != "historical":
            raise ValueError(f"GWL baseline is not historical: {path}")
        identity = _identity(payload)
        if identity in historical:
            raise ValueError(f"duplicate GWL historical baseline: {identity}")
        historical[identity] = (path.resolve(), metadata, payload)

    grouped: dict[float, list[tuple[Path, dict[str, Any], dict[str, Any]]]] = {}
    for level, path in future_specs:
        metadata, payload = _load_summary(path.resolve())
        run = payload["run"]
        if str(run["experiment_id"]) != scenario:
            raise ValueError(f"GWL window has experiment {run['experiment_id']}, expected {scenario}")
        _gwl_level(payload)
        if _identity(payload) not in historical:
            raise ValueError(f"no historical baseline for GWL run {_identity(payload)}")
        grouped.setdefault(float(level), []).append((path.resolve(), metadata, payload))

    # A rerun replaces only earlier generated GWL records. Fixed-window pairs,
    # impact attachments, warming metadata and resolution controls survive.
    records = [pair for pair in index.get("pairs", []) if pair.get("comparison_basis") != "gwl"]
    gwl_index_records: list[dict[str, Any]] = []
    scratch = output_root.parent / ".cmip6-runs" / "gwl-browser-pairs"
    for level in sorted(grouped):
        future_entries = sorted(grouped[level], key=lambda item: _identity(item[2]))
        historical_payloads: list[dict[str, Any]] = []
        future_payloads: list[dict[str, Any]] = []
        individual_ids: list[str] = []
        for future_manifest, _future_metadata, future_payload in future_entries:
            identity = _identity(future_payload)
            historical_manifest, _historical_metadata, historical_payload = historical[identity]
            source, member = identity
            pair_id = _pair_id(source, member, scenario, level)
            pair_output = scratch / f"{source}-{member}-{scenario}-{level:g}"
            change_asset = summarise_pair(historical_manifest, future_manifest, pair_output)
            change_payload = _load_gzip(change_asset)
            comparison = {
                "basis": "gwl",
                "baseline": "1981–2010",
                "level_c": level,
                "scenario": scenario,
                "window_years": int(future_payload["coverage"]["years"]),
                "source": source_url,
            }
            record = {
                "id": pair_id,
                "comparison_basis": "gwl",
                "label": f"{source} · +{level:g} °C GWL · {scenario.upper()}",
                "source_label": source,
                "member_id": member,
                "comparison": comparison,
                "capabilities": {
                    "available_metrics": sorted(
                        set(historical_payload.get("capabilities", {}).get("available_metrics", []))
                        & set(future_payload.get("capabilities", {}).get("available_metrics", []))
                    ),
                    "metric_count": len(
                        set(historical_payload.get("capabilities", {}).get("available_metrics", []))
                        & set(future_payload.get("capabilities", {}).get("available_metrics", []))
                    ),
                    "precipitation_impacts": False,
                },
                "historical": {
                    "run": historical_payload["run"],
                    "coverage": historical_payload["coverage"],
                    "qa": historical_payload.get("qa"),
                    **_browser_asset(output_root, "climate-gwl-historical", historical_payload),
                },
                "future": {
                    "run": {
                        **future_payload["run"],
                        "period_label": f"+{level:g} °C GWL ({future_payload['run']['period_label']})",
                    },
                    "coverage": future_payload["coverage"],
                    "qa": future_payload.get("qa"),
                    **_browser_asset(output_root, "climate-gwl-future", future_payload),
                },
                "change": _browser_asset(output_root, "climate-gwl-change", change_payload),
            }
            records.append(record)
            historical_payloads.append(historical_payload)
            future_payloads.append(future_payload)
            individual_ids.append(pair_id)

        if len(individual_ids) < 2:
            continue
        historical_ensemble = aggregate_run_payloads(
            historical_payloads, role="historical", model_ids=individual_ids
        )
        future_ensemble = aggregate_run_payloads(
            future_payloads, role="future", model_ids=individual_ids
        )
        historical_ensemble["run"]["period_label"] = "1981–2010 model baselines"
        future_ensemble["run"]["period_label"] = f"+{level:g} °C GWL windows"
        change_ensemble = aggregate_change_payloads(
            historical_payloads, future_payloads, model_ids=individual_ids
        )
        metric_names = list(historical_ensemble.get("metric_definitions", {}))
        paired_metric_counts = {
            metric: sum(
                metric in set(left.get("capabilities", {}).get("available_metrics", metric_names))
                and metric in set(right.get("capabilities", {}).get("available_metrics", metric_names))
                for left, right in zip(historical_payloads, future_payloads, strict=True)
            )
            for metric in metric_names
        }
        paired_available_metrics = [
            metric for metric, count in paired_metric_counts.items() if count > 0
        ]
        ensemble_id = hashlib.sha256(
            f"gwl-ensemble|1981-2010|{scenario}|{level:g}|{'|'.join(individual_ids)}".encode("utf-8")
        ).hexdigest()[:16]
        comparison = {
            "basis": "gwl",
            "baseline": "1981–2010",
            "level_c": level,
            "scenario": scenario,
            "window_years": 20,
            "source": source_url,
        }
        records.append(
            {
                "id": ensemble_id,
                "kind": "multi-model",
                "comparison_basis": "gwl",
                "label": f"Multi-model mean · +{level:g} °C GWL · {len(individual_ids)} models",
                "source_label": "Multi-model mean",
                "member_id": "one-model-one-vote",
                "model_ids": individual_ids,
                "comparison": comparison,
                "capabilities": {
                    "available_metrics": paired_available_metrics,
                    "metric_model_counts": paired_metric_counts,
                    "metric_count": len(paired_available_metrics),
                    "precipitation_impacts": False,
                },
                "historical": {
                    "run": historical_ensemble["run"],
                    "coverage": historical_ensemble["coverage"],
                    "qa": historical_ensemble.get("qa"),
                    **_browser_asset(output_root, "climate-gwl-ensemble-historical", historical_ensemble),
                },
                "future": {
                    "run": future_ensemble["run"],
                    "coverage": future_ensemble["coverage"],
                    "qa": future_ensemble.get("qa"),
                    **_browser_asset(output_root, "climate-gwl-ensemble-future", future_ensemble),
                },
                "change": _browser_asset(output_root, "climate-gwl-ensemble-change", change_ensemble),
            }
        )
        gwl_index_records.append(
            {
                "level_c": level,
                "scenario": scenario,
                "ensemble_id": ensemble_id,
                "model_count": len(individual_ids),
                "model_pair_ids": individual_ids,
            }
        )

    records.sort(
        key=lambda pair: (
            1 if pair.get("comparison_basis") == "gwl" else 0,
            float((pair.get("comparison") or {}).get("level_c", 0)),
            0 if pair.get("kind") == "multi-model" else 1,
            str(pair.get("source_label", "")),
        )
    )
    index["pairs"] = records
    index["gwl_comparisons"] = {
        "schema": GWL_SCHEMA,
        "status": "under-construction",
        "baseline": "1981–2010",
        "window_years": 20,
        "crossing_definition": "published IPCC WGI AR6 centred first-crossing windows relative to 1850–1900",
        "source": source_url,
        "comparisons": gwl_index_records,
    }
    index["generated_utc"] = utc_now()
    raw = json.dumps(index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    new_index = output_root / f"climate-index.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
    atomic_gzip_json(new_index, index)
    manifest.update(
        {
            "generated_utc": index["generated_utc"],
            "index": {
                "path": new_index.name,
                "sha256": sha256(new_index),
                "bytes": new_index.stat().st_size,
            },
            "pairs": len(records),
            "gwl_comparisons": len(gwl_index_records),
            "gwl_models_by_level": {
                str(record["level_c"]): record["model_count"] for record in gwl_index_records
            },
        }
    )
    atomic_json(climate_manifest, manifest)
    return new_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--climate-manifest", type=Path, required=True)
    parser.add_argument("--historical-manifest", type=Path, action="append", required=True)
    parser.add_argument(
        "--future-manifest",
        nargs=2,
        action="append",
        metavar=("LEVEL_C", "MANIFEST"),
        required=True,
    )
    parser.add_argument("--scenario", default="ssp245")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    future_specs = [(float(level), Path(path)) for level, path in args.future_manifest]
    print(
        attach_gwl_comparisons(
            args.climate_manifest,
            args.historical_manifest,
            future_specs,
            scenario=args.scenario,
        )
    )


if __name__ == "__main__":
    main()
