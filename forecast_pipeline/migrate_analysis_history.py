#!/usr/bin/env python3
"""Add compact t+0 stitching metadata to an existing forecast service."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
from pathlib import Path
from typing import Any

from .analysis_history import analysis_centres, analysis_entry, replace_analysis_entry
from .forecast_core import atomic_write_json, iso_z, utc_now
from .update import read_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    return parser.parse_args()


def read_payload(path: Path, expected_schema: str) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema") != expected_schema:
        raise ValueError(
            f"{path} has schema {payload.get('schema')!r}, expected {expected_schema!r}"
        )
    return payload


def main() -> None:
    args = parse_args()
    manifest_path = args.target / "manifest.json"
    lock_path = args.target / ".update.lock"
    archive_cycles = live_cycles = 0
    with lock_path.open("a+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        manifest = read_manifest(manifest_path)

        enriched_archive = []
        for entry in manifest.get("archive", []):
            payload = read_payload(
                args.target / str(entry["url"]), "mla-forecast-archive-cycle-v1"
            )
            enriched_archive.append({
                **entry,
                "analysis_centres": analysis_centres(payload),
            })
            archive_cycles += 1
        manifest["archive"] = enriched_archive

        history = manifest.setdefault("analysis_history", {})
        seen: set[tuple[str, str]] = set()
        for collection in (manifest.get("recent", {}), manifest.get("latest", {})):
            for model, raw_entries in collection.items():
                entries = raw_entries if isinstance(raw_entries, list) else [raw_entries]
                for entry in entries:
                    key = (str(model), str(entry.get("cycle", "")))
                    if key in seen:
                        continue
                    seen.add(key)
                    payload = read_payload(
                        args.target / str(entry["url"]), "mla-forecast-cycle-v1"
                    )
                    current = history.setdefault(model, [])
                    history[model] = replace_analysis_entry(
                        current, analysis_entry(payload)
                    )
                    live_cycles += 1

        manifest["analysis_stitch_policy"] = (
            "displayed history uses continuity-matched t+0 centres from the same model "
            "and operational version; live history retains 14 days and archive history "
            "uses the processed archive cadence"
        )
        manifest["analysis_history_generated_utc"] = iso_z(utc_now())
        atomic_write_json(manifest_path, manifest)

    print(
        f"Enriched {archive_cycles} archive cycles and {live_cycles} live cycles in "
        f"{manifest_path}"
    )


if __name__ == "__main__":
    main()
