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


def completed_sources(run_root: Path, collection_key: str) -> dict[tuple[str, str], Path]:
    output: dict[tuple[str, str], Path] = {}
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
                output[key] = root
    return output


def target_state(
    target: Path,
    collection_key: str,
    manifest_key: str,
    expected_plan: tuple[str, int],
) -> tuple[set[tuple[str, str]], str]:
    path = target / "manifest.json"
    if not path.exists():
        return set(), ""
    manifest = read_manifest(path)
    available = {archive_key(entry) for entry in manifest.get(collection_key, [])}
    progress = manifest.get(manifest_key, {})
    identity = (
        str(progress.get("generated_utc", "")),
        int(progress.get("planned_cycles", -1)),
    )
    status = str(progress.get("status", "")) if identity == expected_plan else ""
    return available, status


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
    expected_plan = (str(plan.get("generated_utc", "")), len(plan.get("cycles", [])))
    if not expected_plan[0] or not expected_plan[1]:
        raise ValueError(f"Backfill plan identity is incomplete in {args.plan}")

    while True:
        sources = completed_sources(args.run_root, collection_key)
        available, status = target_state(
            args.target, collection_key, args.manifest_key, expected_plan
        )
        pending = sorted(key for key in sources if key not in available)
        if pending:
            roots = sorted({sources[key] for key in pending})
            LOGGER.info("Publishing %d completed cycle(s) from %d staging root(s)", len(pending), len(roots))
            publish(args.target, args.collection, roots)
            available, status = target_state(
                args.target, collection_key, args.manifest_key, expected_plan
            )
            LOGGER.info("Public collection now contains %d cycle(s)", len(available))

        if args.once:
            return 0
        if status in {"complete", "incomplete"}:
            LOGGER.info("Backfill finalizer reports %s; progressive publisher is finished", status)
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
