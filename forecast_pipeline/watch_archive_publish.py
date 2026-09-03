#!/usr/bin/env python3
"""Publish completed staged archive cycles while a backfill is still running."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .forecast_core import (
    atomic_write_json,
    iso_z,
    ManifestLock,
    manifest_entry_horizon_hours,
    publish_client_manifests,
    utc_now,
)
from .update import read_manifest


LOGGER = logging.getLogger("mla.forecast.watch_archive_publish")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--collection", choices=("archive", "tigge"), required=True)
    parser.add_argument("--manifest-key", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true", help="publish one scan and exit")
    return parser.parse_args()


def archive_key(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get("model", "")), str(entry.get("cycle", ""))


def completed_sources(run_root: Path, collection_key: str) -> dict[tuple[str, str], tuple[Path, int]]:
    output: dict[tuple[str, str], tuple[Path, int]] = {}
    for path in sorted(run_root.glob("*/manifest.json")):
        root = path.parent
        try:
            manifest = read_manifest(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            LOGGER.warning("Ignoring unreadable staged manifest %s: %s", path, error)
            continue
        for entry in manifest.get(collection_key, []):
            key = archive_key(entry)
            if all(key):
                output[key] = (root, manifest_entry_horizon_hours(entry))
    return output


def target_state(
    target: Path,
    collection_key: str,
    manifest_key: str,
    expected_plan: tuple[str, int],
    required_weather_fields: set[str] | None = None,
) -> tuple[dict[tuple[str, str], int], str]:
    path = target / "manifest.json"
    if not path.exists():
        return {}, ""
    manifest = read_manifest(path)
    required_weather = required_weather_fields or set()
    available = {
        archive_key(entry): manifest_entry_horizon_hours(entry)
        for entry in manifest.get(collection_key, [])
        if required_weather.issubset(set(entry.get("weather_fields", [])))
    }
    progress = manifest.get(manifest_key, {})
    identity = (
        str(progress.get("generated_utc", "")),
        int(progress.get("planned_cycles", -1)),
    )
    status = str(progress.get("status", "")) if identity == expected_plan else ""
    return available, status


def update_progress(
    target: Path,
    collection_key: str,
    manifest_key: str,
    plan: dict[str, Any],
) -> None:
    """Atomically advertise the current plan without overwriting final status."""

    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    with ManifestLock(target):
        manifest = read_manifest(manifest_path)
        expected_identity = (str(plan.get("generated_utc", "")), len(plan.get("cycles", [])))
        existing = manifest.get(manifest_key, {})
        existing_identity = (
            str(existing.get("generated_utc", "")),
            int(existing.get("planned_cycles", -1)),
        )
        if existing_identity == expected_identity and existing.get("status") in {"complete", "incomplete"}:
            return

        planned = {
            (str(item.get("model", "")), str(item.get("cycle", ""))): int(item.get("horizon_hours", 0))
            for item in plan.get("cycles", [])
        }
        required_weather = set(plan.get("required_weather_fields", []))
        available = {
            archive_key(entry): manifest_entry_horizon_hours(entry)
            for entry in manifest.get(collection_key, [])
            if required_weather.issubset(set(entry.get("weather_fields", [])))
        }
        complete_keys = {
            key
            for key, horizon in planned.items()
            if available.get(key, -1) >= horizon
        }
        now = iso_z(utc_now())
        manifest[manifest_key] = {
            **{key: value for key, value in plan.items() if key not in {"cycles", "pending_cycles"}},
            "status": "running",
            "planned_cycles": len(planned),
            "available_cycles": len(complete_keys),
            "published_utc": now,
        }
        manifest["generated_utc"] = now
        atomic_write_json(manifest_path, manifest)
        publish_client_manifests(target, manifest)


def publish(target: Path, collection: str, sources: list[Path]) -> None:
    command = [
        sys.executable,
        "-m",
        "forecast_pipeline.merge_archives",
        "--target",
        str(target),
        "--collection",
        collection,
        *[str(source) for source in sources],
    ]
    subprocess.run(command, check=True)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
    if args.poll_seconds <= 0:
        raise ValueError("--poll-seconds must be positive")
    collection_key = "tigge_archive" if args.collection == "tigge" else "archive"
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    required_weather = set(plan.get("required_weather_fields", []))
    expected_plan = (str(plan.get("generated_utc", "")), len(plan.get("cycles", [])))
    if not expected_plan[0] or not expected_plan[1]:
        raise ValueError(f"Backfill plan identity is incomplete in {args.plan}")
    update_progress(args.target, collection_key, args.manifest_key, plan)

    while True:
        sources = completed_sources(args.run_root, collection_key)
        available, status = target_state(
            args.target,
            collection_key,
            args.manifest_key,
            expected_plan,
            required_weather,
        )
        pending = sorted(
            key
            for key, (unused_root, horizon) in sources.items()
            if available.get(key, -1) < horizon
        )
        if pending:
            roots = sorted({sources[key][0] for key in pending})
            LOGGER.info("Publishing %d completed cycle(s) from %d staging root(s)", len(pending), len(roots))
            publish(args.target, args.collection, roots)
            update_progress(args.target, collection_key, args.manifest_key, plan)
            available, status = target_state(
                args.target,
                collection_key,
                args.manifest_key,
                expected_plan,
                required_weather,
            )
            LOGGER.info("Public collection now contains %d cycle(s)", len(available))

        if args.once:
            return 0
        if status == "complete":
            LOGGER.info("Backfill finalizer reports %s; progressive publisher is finished", status)
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
