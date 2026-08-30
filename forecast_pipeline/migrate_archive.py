#!/usr/bin/env python3
"""Certify existing compact archives and attach cycle-specific versions."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
from datetime import datetime
from pathlib import Path

from .archive import archive_manifest_entry, certify_archive_payload
from .forecast_core import atomic_write_json, atomic_write_json_gz, iso_z, utc_now
from .update import read_manifest, replace_archive_entry
from .versions import model_version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = args.target / "manifest.json"
    with (args.target / ".update.lock").open("a+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        manifest = read_manifest(manifest_path)
        rebuilt = []
        for entry in manifest.get("archive", []):
            path = args.target / str(entry["url"])
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("schema") != "mla-forecast-archive-cycle-v1":
                raise ValueError(f"Unexpected schema in {path}")
            if "weather" in payload or "tracking_qa" in payload:
                raise ValueError(f"Compact archive contains forbidden grids/QA: {path}")
            cycle = datetime.fromisoformat(str(payload["cycle_utc"]).replace("Z", "+00:00"))
            payload["model_version"] = model_version(str(payload["model"]["id"]), cycle)
            certify_archive_payload(payload)
            atomic_write_json_gz(path, payload)
            rebuilt.append(archive_manifest_entry(payload, str(entry["url"])))
        manifest["archive"] = []
        for entry in rebuilt:
            manifest["archive"] = replace_archive_entry(manifest["archive"], entry)
        manifest["archive_metadata_migration"] = {
            "migrated_utc": iso_z(utc_now()),
            "cycles": len(rebuilt),
            "changes": "added complete-valid-time certification and documented operational model-generation labels",
        }
        manifest["generated_utc"] = iso_z(utc_now())
        atomic_write_json(manifest_path, manifest)
    print(f"Certified {len(rebuilt)} existing archive cycles in {manifest_path}")


if __name__ == "__main__":
    main()
