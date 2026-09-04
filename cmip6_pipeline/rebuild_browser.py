#!/usr/bin/env python3
"""Rebuild the source-backed climate-change browser bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from reanalysis_pipeline.common import sha256

from .gwl_publish import attach_gwl_comparisons
from .impact import attach_to_climate_bundle as attach_impacts
from .publish_control import attach_resolution_control
from .summarise import (
    INDEX_SCHEMA,
    assemble_ensemble,
    atomic_gzip_json,
    atomic_json,
    publish_pair,
    summarise_pair,
    summarise_run,
    utc_now,
)
from .warming import attach_to_climate_bundle as attach_warming
from .warming import build_registry as build_warming_registry


FIXED_PAIR_DIRECTORIES = (
    "hadgem-ll-paired",
    "miroc6-paired",
    "mpi-esm1-2-hr-production",
    "mpi-lr-paired",
    "mri-paired",
)
ADDITIONAL_PAIR_DIRECTORIES = (
    "hadgem-mm-ssp126-paired",
    "hadgem-mm-ssp585-paired",
)
HIGHRES_PAIR_DIRECTORIES = (
    "highres-cnrm-paired",
    "highres-ecearth-paired",
    "highres-ecearth-hr-paired",
)


def _load_gzip(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _manifest_index(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"unsupported climate manifest: {manifest_path}")
    index_path = manifest_path.parent / manifest["index"]["path"]
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError(f"climate index checksum does not match: {index_path}")
    index = _load_gzip(index_path)
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"unsupported climate index: {index_path}")
    return manifest, index, manifest_path.parent


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def attach_comparison_bundle(
    target_manifest_path: Path,
    source_manifest_path: Path,
    *,
    collection_id: str,
    collection_label: str,
) -> Path:
    """Append a separately aggregated experiment family to the browser bundle."""

    target_manifest, target_index, target_root = _manifest_index(target_manifest_path)
    source_manifest, source_index, source_root = _manifest_index(source_manifest_path)
    existing = {str(pair["id"]): pair for pair in target_index.get("pairs", [])}
    for original in source_index.get("pairs", []):
        record = json.loads(json.dumps(original))
        pair_id = str(record["id"])
        is_new = pair_id not in existing
        if not is_new and existing[pair_id] != record:
            raise ValueError(f"conflicting climate comparison {pair_id}")
        for role in ("historical", "future", "change", "impact"):
            reference = record.get(role) or {}
            if not reference:
                continue
            source = source_root / reference["url"]
            if sha256(source) != reference["sha256"]:
                raise ValueError(f"{role} checksum does not match for {pair_id}")
            destination = target_root / reference["url"]
            if not destination.is_file() or sha256(destination) != reference["sha256"]:
                _copy_atomic(source, destination)
        if is_new:
            target_index.setdefault("pairs", []).append(record)
            existing[pair_id] = record

    source_ensemble = source_index.get("ensemble") or {}
    if str(source_ensemble.get("id", "")) not in existing:
        raise ValueError(f"{collection_id} source bundle has no ensemble comparison")
    collections = [
        item
        for item in target_index.get("comparison_collections", [])
        if item.get("id") != collection_id
    ]
    collections.append(
        {
            "id": collection_id,
            "label": collection_label,
            "ensemble_id": source_ensemble["id"],
            "model_count": source_ensemble["model_count"],
            "included_pair_ids": source_ensemble["included_pair_ids"],
            "status": source_index.get("status"),
        }
    )
    target_index["comparison_collections"] = collections
    target_index["generated_utc"] = utc_now()
    raw = json.dumps(target_index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    new_index = target_root / f"climate-index.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
    atomic_gzip_json(new_index, target_index)
    target_manifest.update(
        {
            "generated_utc": target_index["generated_utc"],
            "index": {
                "path": new_index.name,
                "sha256": sha256(new_index),
                "bytes": new_index.stat().st_size,
            },
            "pairs": len(target_index["pairs"]),
            f"{collection_id}_models": int(source_ensemble["model_count"]),
            f"{collection_id}_schema": source_manifest.get("schema"),
        }
    )
    atomic_json(target_manifest_path, target_manifest)
    return new_index


def period_manifests(pair_root: Path) -> list[Path]:
    paths = sorted(pair_root.glob("*/physics/summary/manifest.json"))
    if len(paths) != 2:
        raise ValueError(f"expected two run summaries below {pair_root}, found {len(paths)}")
    return paths


def refresh_summary(manifest: Path) -> Path:
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    run = metadata["run"]
    coverage = metadata["coverage"]
    physics_root = manifest.parent.parent
    catalogue = physics_root / "cmip6-physical-events.parquet"
    qa_report = physics_root / "qa-summary.json"
    if not catalogue.is_file() or not qa_report.is_file():
        raise FileNotFoundError(f"summary inputs are incomplete below {physics_root}")
    summarise_run(
        catalogue,
        manifest.parent,
        source_label=run["source_label"],
        experiment_id=run["experiment_id"],
        member_id=run["member_id"],
        period_label=run["period_label"],
        start_year=int(coverage["start_year"]),
        end_year=int(coverage["end_year"]),
        qa_report=qa_report,
    )
    return manifest


def refresh_pair(pair_root: Path) -> tuple[Path, str]:
    manifests = period_manifests(pair_root)
    by_experiment = {
        json.loads(path.read_text(encoding="utf-8"))["run"]["experiment_id"]: path
        for path in manifests
    }
    baseline_keys = [key for key in ("historical", "hist-1950") if key in by_experiment]
    if len(baseline_keys) != 1:
        raise ValueError(f"expected one historical baseline below {pair_root}")
    historical = by_experiment.pop(baseline_keys[0])
    if len(by_experiment) != 1:
        raise ValueError(f"expected one future experiment below {pair_root}")
    future = next(iter(by_experiment.values()))
    pair_output = pair_root / "climate-summary"
    summarise_pair(historical, future, pair_output)
    public_output = pair_root / "climate-public"
    index_path = publish_pair(historical, future, pair_output / "manifest.json", public_output)
    index = _load_gzip(index_path)
    if len(index["pairs"]) != 1:
        raise ValueError(f"single-pair publication below {pair_root} is ambiguous")
    return public_output / "manifest.json", str(index["pairs"][0]["id"])


def gwl_manifests(gwl_root: Path) -> list[tuple[float, Path]]:
    records: list[tuple[float, Path]] = []
    for path in sorted(gwl_root.glob("*/*/physics/summary/manifest.json")):
        directory = path.parents[3].name
        marker = directory.rsplit("gwl", 1)[-1]
        try:
            level = float(marker.replace("p", "."))
        except ValueError as error:
            raise ValueError(f"cannot read GWL from {directory}") from error
        records.append((level, path))
    if not records:
        raise FileNotFoundError(f"no completed GWL summaries below {gwl_root}")
    return records


def rebuild(repo_root: Path, *, refresh: bool = True) -> Path:
    repo_root = repo_root.resolve()
    run_root = repo_root / ".cmip6-runs"
    fixed_roots = [run_root / name for name in FIXED_PAIR_DIRECTORIES]
    extra_roots = [
        run_root / name
        for name in ADDITIONAL_PAIR_DIRECTORIES
        if (run_root / name).is_dir()
    ]
    pair_roots = fixed_roots + extra_roots
    gwl_records = gwl_manifests(run_root / "gwl-ssp245")
    control_manifest = (
        run_root
        / "era5-common-grid-control"
        / "era5-1deg-control-historical-analysis-common-1deg"
        / "physics"
        / "summary"
        / "manifest.json"
    )

    if refresh:
        for pair_root in pair_roots:
            for manifest in period_manifests(pair_root):
                refresh_summary(manifest)
        for _level, manifest in gwl_records:
            refresh_summary(manifest)
        refresh_summary(control_manifest)

    public_manifests: list[Path] = []
    fixed_ids: list[str] = []
    for pair_root in pair_roots:
        public_manifest, pair_id = refresh_pair(pair_root)
        public_manifests.append(public_manifest)
        if pair_root in fixed_roots:
            fixed_ids.append(pair_id)

    output_root = repo_root / "climate-change"
    assemble_ensemble(
        public_manifests,
        output_root,
        include_pair_ids=fixed_ids,
        status="multi-model-awaiting-review",
    )

    impacts = [root / "climate-impact" / "manifest.json" for root in fixed_roots]
    if all(path.is_file() for path in impacts):
        attach_impacts(output_root / "manifest.json", impacts)

    attach_resolution_control(output_root / "manifest.json", control_manifest)

    warming_output = run_root / "global-warming-browser"
    warming_manifest = build_warming_registry(pair_roots, warming_output)
    attach_warming(output_root / "manifest.json", warming_manifest)

    historical_manifests = []
    for pair_root in fixed_roots:
        historical_manifests.append(
            next(
                path
                for path in period_manifests(pair_root)
                if json.loads(path.read_text(encoding="utf-8"))["run"]["experiment_id"]
                == "historical"
            )
        )
    attach_gwl_comparisons(
        output_root / "manifest.json",
        historical_manifests,
        gwl_records,
        scenario="ssp245",
    )

    highres_roots = [run_root / name for name in HIGHRES_PAIR_DIRECTORIES]
    completed_highres = [
        root
        for root in highres_roots
        if len(list(root.glob("*/physics/summary/manifest.json"))) == 2
    ]
    if completed_highres:
        if len(completed_highres) != len(highres_roots):
            missing = sorted(root.name for root in set(highres_roots) - set(completed_highres))
            raise ValueError(f"HighResMIP production family is incomplete: {missing}")
        if refresh:
            for pair_root in highres_roots:
                for manifest in period_manifests(pair_root):
                    refresh_summary(manifest)
        highres_public: list[Path] = []
        highres_ids: list[str] = []
        for pair_root in highres_roots:
            public_manifest, pair_id = refresh_pair(pair_root)
            highres_public.append(public_manifest)
            highres_ids.append(pair_id)
        highres_output = run_root / "highresmip-browser"
        assemble_ensemble(
            highres_public,
            highres_output,
            include_pair_ids=highres_ids,
            status="multi-model-awaiting-review",
        )
        highres_impacts = [root / "climate-impact" / "manifest.json" for root in highres_roots]
        if all(path.is_file() for path in highres_impacts):
            attach_impacts(highres_output / "manifest.json", highres_impacts)
        return attach_comparison_bundle(
            output_root / "manifest.json",
            highres_output / "manifest.json",
            collection_id="highresmip",
            collection_label="HighResMIP mid-century",
        )
    return output_root / json.loads(
        (output_root / "manifest.json").read_text(encoding="utf-8")
    )["index"]["path"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--skip-summary-refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(rebuild(args.repo_root, refresh=not args.skip_summary_refresh))


if __name__ == "__main__":
    main()
