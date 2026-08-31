#!/usr/bin/env python3
"""Plan tracker jobs for complete cycle caches downloaded from CMA TIGGE."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .update import read_manifest


GRIB_SUFFIXES = {".grib", ".grb", ".grib2", ".grb2"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--recovery-plan", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    return parser.parse_args()


def _has_grib(path: Path) -> bool:
    return path.is_dir() and any(
        item.is_file() and item.suffix.lower() in GRIB_SUFFIXES
        for item in path.rglob("*")
    )


def main() -> None:
    args = parse_args()
    recovery = json.loads(args.recovery_plan.read_text(encoding="utf-8"))
    requested: dict[tuple[str, str], dict[str, object]] = {}
    for item in recovery.get("requests", []):
        model, cycle = str(item["model"]), str(item["cycle"])
        steps = [int(value) for value in item.get("request", {}).get("steps", [])]
        if not steps:
            continue
        requested[(model, cycle)] = {
            "model": model,
            "cycle": cycle,
            "first_step_hours": min(steps),
            "horizon_hours": max(steps),
        }
    manifest = read_manifest(args.manifest)
    available = {
        (str(item.get("model")), str(item.get("cycle"))): manifest_entry_horizon_hours(item)
        for item in manifest.get("tigge_archive", [])
    }
    ready = []
    incomplete = []
    for key, item in sorted(requested.items()):
        root = args.cache_root / key[0] / key[1]
        if not (_has_grib(root / "pressure") and _has_grib(root / "surface")):
            incomplete.append({"model": key[0], "cycle": key[1]})
            continue
        if available.get(key, -1) < int(item["horizon_hours"]):
            ready.append(item)
    plan = {
        "schema": "mla-cma-tigge-cache-processing-plan-v1",
        "manifest_key": "tigge_cma_recovery",
        "generated_utc": iso_z(utc_now()),
        "cache_root": str(args.cache_root),
        "recovery_plan": str(args.recovery_plan),
        "ready_cycles": len(ready),
        "incomplete_cache_cycles": len(incomplete),
        "incomplete": incomplete,
        "cycles": ready,
        "pending_cycles": ready,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(
            f"{index}\t{item['model']}\t{item['cycle']}\t{item['horizon_hours']}\t{item['first_step_hours']}\n"
            for index, item in enumerate(ready, 1)
        ),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(f"Planned {len(ready)} CMA-cached cycles; {len(incomplete)} requested cycles are not fully staged")


if __name__ == "__main__":
    main()
