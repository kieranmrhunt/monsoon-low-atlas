#!/usr/bin/env python3
"""Merge validated public forecast archive cycles."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import shutil
from datetime import datetime
from pathlib import Path

from .forecast_core import (
    atomic_write_json,
    atomic_write_json_gz,
    iso_z,
    manifest_entry_horizon_hours,
    utc_now,
)
from .update import read_manifest, replace_archive_entry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument(
        "--nonblocking-lock",
        action="store_true",
        help="leave validated staging in place and exit successfully when another publisher owns the target lock",
    )
    parser.add_argument("--collection", choices=("archive", "tigge"), default="archive")
    parser.add_argument("sources", nargs="*", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = list(args.sources)
    if args.run_root:
        sources.extend(path.parent for path in sorted(args.run_root.glob("*/manifest.json")))
    sources = sorted(set(path.resolve() for path in sources))
    if not sources:
        raise RuntimeError("No completed archive source manifests were found")
    source_manifests = [read_manifest(source / "manifest.json") for source in sources]
    collection_key = "tigge_archive" if args.collection == "tigge" else "archive"
    target_path = args.target / "manifest.json"
    args.target.mkdir(parents=True, exist_ok=True)
    with (args.target / ".update.lock").open("a+") as lock_stream:
        lock_mode = fcntl.LOCK_EX | (fcntl.LOCK_NB if args.nonblocking_lock else 0)
        try:
            fcntl.flock(lock_stream.fileno(), lock_mode)
        except BlockingIOError:
            print(
                f"Another archive publisher is active; retained {len(sources)} validated staging source(s)"
            )
            return
        if target_path.exists():
            manifest = read_manifest(target_path)
        else:
            manifest = dict(source_manifests[0])
            manifest["latest"] = {}
            manifest["attempts"] = {}
            manifest["archive"] = []
            manifest["tigge_archive"] = []

        existing_entries = {
            (str(item.get("model", "")), str(item.get("cycle", ""))): item
            for item in manifest.get(collection_key, [])
        }
        merged: list[str] = []
        for source_root, source_manifest in zip(sources, source_manifests, strict=True):
            for entry in source_manifest.get(collection_key, []):
                relative = str(entry["url"])
                with gzip.open(source_root / relative, "rt", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if payload.get("schema") != "mla-forecast-archive-cycle-v1":
                    raise ValueError(f"{source_root / relative} is not a public archive cycle")
                if "tracking_qa" in payload:
                    raise ValueError(f"{source_root / relative} contains internal tracking QA")
                if args.collection == "tigge" and "weather" in payload:
                    raise ValueError(f"{source_root / relative} contains weather forbidden from TIGGE")
                if not payload.get("archive_coverage", {}).get("complete_valid_time_axis"):
                    raise ValueError(f"{source_root / relative} does not certify its complete valid-time axis")
                entry_key = (str(entry.get("model", "")), str(entry.get("cycle", "")))
                existing = existing_entries.get(entry_key)
                already_complete = (
                    existing is not None
                    and manifest_entry_horizon_hours(existing) >= manifest_entry_horizon_hours(entry)
                    and (args.target / str(existing.get("url", ""))).exists()
                )
                if not already_complete:
                    atomic_write_json_gz(args.target / relative, payload)
                    manifest[collection_key] = replace_archive_entry(
                        manifest.setdefault(collection_key, []), entry
                    )
                    existing_entries[entry_key] = entry
                merged.append(f"{entry['model']}:{entry['cycle']}")

        for key in (
            "schema", "schedule", "weather_archive_policy", "forecast_horizon_policy",
            "catalogue_verification", "models", "source_notes",
        ):
            if key in source_manifests[0]:
                manifest[key] = source_manifests[0][key]
        manifest["generated_utc"] = iso_z(utc_now())
        horizon_groups: dict[str, set[int]] = {}
        for entry in manifest.get(collection_key, []):
            horizon_groups.setdefault(str(entry.get("model", "")), set()).add(
                manifest_entry_horizon_hours(entry)
            )
        manifest[f"{collection_key}_horizons_hours"] = {
            model: sorted(values) for model, values in sorted(horizon_groups.items()) if model
        }
        seed_key = "tigge_seed" if args.collection == "tigge" else "archive_seed"
        previous_cases = manifest.get(seed_key, {}).get("cases", [])
        manifest[seed_key] = {
            "merged_utc": manifest["generated_utc"],
            "cases": sorted(set(previous_cases) | set(merged)),
            "policy": (
                "complete valid-time axes, all published tracks and ensemble-mean weather; internal QA omitted"
                if args.collection == "archive"
                else "complete valid-time axes and all published tracks; weather and internal QA omitted"
            ),
        }
        complete = True
        if args.plan:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            planned = {
                f"{item['model']}:{item['cycle']}": (
                    int(item.get("horizon_hours", 0)),
                    int(item.get("first_step_hours", 0)),
                )
                for item in plan["cycles"]
            }
            available = {}
            for item in manifest.get(collection_key, []):
                cycle_time = datetime.fromisoformat(str(item["cycle_utc"]).replace("Z", "+00:00"))
                valid_start = datetime.fromisoformat(
                    str(item.get("valid_start_utc", item["cycle_utc"])).replace("Z", "+00:00")
                )
                available[f"{item.get('model')}:{item.get('cycle')}"] = (
                    manifest_entry_horizon_hours(item),
                    int(round((valid_start - cycle_time).total_seconds() / 3600)),
                )
            complete_keys = {
                key
                for key, (horizon, first_step) in planned.items()
                if available.get(key, (-1, 999))[0] >= horizon
                and available.get(key, (-1, 999))[1] <= first_step
            }
            missing = sorted(set(planned) - complete_keys)
            complete = not missing
            manifest_key = str(plan.get("manifest_key", "archive_backfill"))
            manifest[manifest_key] = {
                **{key: value for key, value in plan.items() if key not in {"cycles", "pending_cycles"}},
                "status": "complete" if complete else "incomplete",
                "planned_cycles": len(planned),
                "available_cycles": len(complete_keys),
                "missing_cycles": missing,
                "merged_utc": manifest["generated_utc"],
            }
        atomic_write_json(target_path, manifest)
    print(f"Merged {len(set(merged))} archive cases into {target_path}")
    if args.cleanup and complete and args.run_root:
        resolved = args.run_root.resolve()
        if ".forecast-runs" not in resolved.parts:
            raise ValueError(f"Refusing to remove unexpected staging path {resolved}")
        shutil.rmtree(resolved)
    if not complete:
        raise SystemExit("Archive backfill is incomplete; staging was retained for retry")


if __name__ == "__main__":
    main()
