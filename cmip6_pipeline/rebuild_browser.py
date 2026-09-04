#!/usr/bin/env python3
"""Rebuild the source-backed climate-change browser bundle."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from .gwl_publish import attach_gwl_comparisons
from .impact import attach_to_climate_bundle as attach_impacts
from .publish_control import attach_resolution_control
from .summarise import assemble_ensemble, publish_pair, summarise_pair, summarise_run
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


def _load_gzip(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


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
    historical = by_experiment.pop("historical")
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
    return attach_gwl_comparisons(
        output_root / "manifest.json",
        historical_manifests,
        gwl_records,
        scenario="ssp245",
    )


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
