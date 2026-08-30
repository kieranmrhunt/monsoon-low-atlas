#!/usr/bin/env python3
"""Merge validated compact forecast archives without copying weather grids."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from .forecast_core import atomic_write_json, atomic_write_json_gz, iso_z, utc_now
from .update import read_manifest, replace_archive_entry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("sources", nargs="+", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_manifests = [read_manifest(source / "manifest.json") for source in args.sources]
    target_path = args.target / "manifest.json"
    if target_path.exists():
        manifest = read_manifest(target_path)
    else:
        manifest = dict(source_manifests[0])
        manifest["latest"] = {}
        manifest["attempts"] = {}
        manifest["archive"] = []

    merged: list[str] = []
    for source_root, source_manifest in zip(args.sources, source_manifests, strict=True):
        for entry in source_manifest.get("archive", []):
            relative = str(entry["url"])
            with gzip.open(source_root / relative, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("schema") != "mla-forecast-archive-cycle-v1":
                raise ValueError(f"{source_root / relative} is not a compact archive cycle")
            if "weather" in payload or "tracking_qa" in payload:
                raise ValueError(f"{source_root / relative} contains fields forbidden from the archive")
            atomic_write_json_gz(args.target / relative, payload)
            manifest["archive"] = replace_archive_entry(manifest.setdefault("archive", []), entry)
            merged.append(f"{entry['model']}:{entry['cycle']}")

    for key in (
        "schema", "schedule", "forecast_horizon_hours", "weather_archive_policy",
        "catalogue_verification", "models", "source_notes",
    ):
        if key not in manifest and key in source_manifests[0]:
            manifest[key] = source_manifests[0][key]
    manifest["generated_utc"] = iso_z(utc_now())
    manifest["archive_seed"] = {
        "merged_utc": manifest["generated_utc"],
        "cases": sorted(set(merged)),
        "policy": "tracks and ERA5 verification only; weather omitted",
    }
    atomic_write_json(target_path, manifest)
    print(f"Merged {len(set(merged))} archive cases into {target_path}")


if __name__ == "__main__":
    main()
