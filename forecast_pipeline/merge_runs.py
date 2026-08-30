#!/usr/bin/env python3
"""Atomically merge independent model runs into the public forecast service."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .analysis_history import analysis_centres, analysis_entry, replace_analysis_entry
from .forecast_core import (
    atomic_write_json,
    atomic_write_json_gz,
    iso_z,
    manifest_entry_horizon_hours,
    utc_now,
)
from .sources import DEFAULT_MODELS, MODEL_DEFINITIONS
from .update import read_manifest, replace_archive_entry, replace_recent_entry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument("--partial", action="store_true", help="merge a cycle backfill without marking unsubmitted models failed")
    parser.add_argument("--plan", type=Path, help="audit a recent-cycle backfill plan after merging")
    return parser.parse_args()


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def copy_payload(source: Path, target: Path, schema: str) -> dict[str, Any]:
    payload = read_gzip_json(source)
    if payload.get("schema") != schema:
        raise ValueError(f"{source} has schema {payload.get('schema')!r}, expected {schema!r}")
    atomic_write_json_gz(target, payload)
    return payload


def clean_superseded_weather(target: Path, manifest: dict[str, Any]) -> None:
    for model, entry in manifest.get("latest", {}).items():
        cycle_dir = target / "cycles" / model
        keep = {
            cycle_dir / Path(str(item.get("url", ""))).name
            for item in manifest.get("recent", {}).get(model, [])
        }
        keep.add(cycle_dir / Path(str(entry.get("url", ""))).name)
        if not cycle_dir.is_dir():
            continue
        for candidate in cycle_dir.glob("*.json.gz"):
            if candidate not in keep:
                candidate.unlink()


def main() -> None:
    args = parse_args()
    if not args.run_root.is_dir():
        raise FileNotFoundError(args.run_root)
    source_paths = sorted(args.run_root.glob("*/manifest.json"))
    if not source_paths:
        raise RuntimeError(f"No completed model manifests below {args.run_root}")

    args.target.mkdir(parents=True, exist_ok=True)
    lock_path = args.target / ".update.lock"
    complete = True
    with lock_path.open("a+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        target_manifest_path = args.target / "manifest.json"
        manifest = read_manifest(target_manifest_path)
        successful: list[str] = []
        attempted: list[str] = []
        source_manifests: list[dict[str, Any]] = []
        staged_models: set[str] = set()

        for source_path in source_paths:
            source_root = source_path.parent
            source_manifest = read_manifest(source_path)
            source_manifests.append(source_manifest)
            staged_models.update(source_manifest.get("attempts", {}))
            for model, attempt in source_manifest.get("attempts", {}).items():
                attempted.append(model)
                if not args.partial:
                    manifest.setdefault("attempts", {})[model] = attempt
            for model, entry in source_manifest.get("latest", {}).items():
                relative = str(entry["url"])
                payload = copy_payload(
                    source_root / relative,
                    args.target / relative,
                    "mla-forecast-cycle-v1",
                )
                if payload.get("qa", {}).get("status") == "failed":
                    raise ValueError(f"{model} latest payload failed its embedded QA")
                previous = manifest.setdefault("latest", {}).get(model)
                if previous is None or str(previous.get("cycle", "")) <= str(entry.get("cycle", "")):
                    manifest["latest"][model] = entry
                history = manifest.setdefault("analysis_history", {}).setdefault(model, [])
                manifest["analysis_history"][model] = replace_analysis_entry(
                    history, analysis_entry(payload)
                )
                successful.append(model)
            for model, entries in source_manifest.get("recent", {}).items():
                for entry in entries:
                    relative = str(entry["url"])
                    payload = copy_payload(
                        source_root / relative,
                        args.target / relative,
                        "mla-forecast-cycle-v1",
                    )
                    if payload.get("qa", {}).get("status") == "failed":
                        raise ValueError(f"{model} recent payload failed its embedded QA")
                    current = manifest.setdefault("recent", {}).setdefault(model, [])
                    manifest["recent"][model] = replace_recent_entry(current, entry)
                    history = manifest.setdefault("analysis_history", {}).setdefault(model, [])
                    manifest["analysis_history"][model] = replace_analysis_entry(
                        history, analysis_entry(payload)
                    )
            for entry in source_manifest.get("archive", []):
                relative = str(entry["url"])
                payload = copy_payload(
                    source_root / relative,
                    args.target / relative,
                    "mla-forecast-archive-cycle-v1",
                )
                if "tracking_qa" in payload:
                    raise ValueError(f"{source_root / relative} contains internal tracking QA")
                enriched_entry = {**entry, "analysis_centres": analysis_centres(payload)}
                manifest["archive"] = replace_archive_entry(
                    manifest.setdefault("archive", []), enriched_entry
                )

        missing_models = [] if args.partial else sorted(set(DEFAULT_MODELS) - staged_models)
        for model in missing_models:
            attempted.append(model)
            manifest.setdefault("attempts", {})[model] = {
                "status": "failed",
                "attempted_utc": iso_z(utc_now()),
                "message": "model job produced no staging manifest; previous Latest cycle retained",
            }

        reference = source_manifests[0]
        for key in (
            "schema", "schedule", "weather_archive_policy",
            "analysis_stitch_policy", "catalogue_verification", "source_notes",
        ):
            if key in reference:
                manifest[key] = reference[key]
        # Provider endpoints can change independently of archived forecast
        # cycles.  Always publish the definitions used by the currently
        # deployed updater rather than inheriting stale labels from a seed or
        # earlier per-model manifest.
        manifest["models"] = [
            asdict(MODEL_DEFINITIONS[model]) for model in DEFAULT_MODELS
        ]
        manifest["forecast_horizons_hours"] = {
            model: manifest_entry_horizon_hours(entry)
            for model, entry in manifest.get("latest", {}).items()
        }
        manifest["forecast_horizon_hours"] = max(
            manifest["forecast_horizons_hours"].values(), default=None
        )
        manifest["forecast_horizon_policy"] = "complete provider/model lead axis"
        manifest["generated_utc"] = iso_z(utc_now())
        manifest["run"] = {
            "mode": "partial-cycle-backfill" if args.partial else "parallel-model-merge",
            "attempted_models": sorted(set(attempted)),
            "successful_models": sorted(set(successful)),
            "source_manifests": len(source_manifests),
        }
        if args.plan:
            backfill_plan = json.loads(args.plan.read_text(encoding="utf-8"))
            planned = {
                f"{item['model']}:{item['cycle']}": int(item.get("horizon_hours", 0))
                for item in backfill_plan["cycles"]
            }
            available = {
                f"{model}:{item.get('cycle')}": manifest_entry_horizon_hours(item)
                for model, entries in manifest.get("recent", {}).items()
                for item in entries
            }
            complete_keys = {
                key
                for key, horizon in planned.items()
                if available.get(key, -1) >= horizon
            }
            missing = sorted(set(planned) - complete_keys)
            complete = not missing
            manifest["recent_backfill"] = {
                **{key: value for key, value in backfill_plan.items() if key not in {"cycles", "pending_cycles"}},
                "status": "complete" if complete else "incomplete",
                "planned_cycles": len(planned),
                "available_cycles": len(complete_keys),
                "missing_cycles": missing,
                "merged_utc": manifest["generated_utc"],
            }
        atomic_write_json(target_manifest_path, manifest)
        clean_superseded_weather(args.target, manifest)

    if not args.keep_staging and complete:
        resolved = args.run_root.resolve()
        if ".forecast-runs" not in resolved.parts:
            raise ValueError(f"Refusing to remove unexpected staging path {resolved}")
        shutil.rmtree(resolved)
    print(
        f"Published {len(set(successful))}/{len(set(attempted))} successful model runs "
        f"to {target_manifest_path}"
    )
    if not complete:
        raise SystemExit("Recent-cycle backfill is incomplete; staging was retained for retry")


if __name__ == "__main__":
    main()
