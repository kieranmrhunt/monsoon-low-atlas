#!/usr/bin/env python3
"""Publish validated matched-track assets and their browser inventory."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .common import sha256
from .match import MATCH_SCHEMA


MANIFEST_SCHEMA = "lps-atlas-reanalysis-manifest-v1"
SOURCE_LABELS = {"merra2": "MERRA-2", "imdaa": "IMDAA"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def validate_match_asset(path: Path, source: str) -> dict[str, Any]:
    value = read_gzip_json(path)
    if value.get("schema") != MATCH_SCHEMA or str(value.get("source", "")).lower() != source:
        raise ValueError(f"{path} is not a {source} {MATCH_SCHEMA} asset")
    if not isinstance(value.get("matches"), list) or not isinstance(value.get("tracks"), dict):
        raise ValueError(f"{path} has no matched-track payload")
    track_ids = {str(item.get("source_track_id")) for item in value["matches"]}
    if not track_ids.issubset(value["tracks"]):
        raise ValueError(f"{path} omits geometry for one or more selected matches")
    return value


def source_record(source: str, path: Path, destination_name: str) -> dict[str, Any]:
    value = validate_match_asset(path, source)
    qa = value.get("qa", {})
    return {
        "label": SOURCE_LABELS[source],
        "status": "ready",
        "matches_url": destination_name,
        "coverage_start_utc": value.get("coverage_start_utc"),
        "coverage_end_utc": value.get("coverage_end_utc"),
        "matched_era5_events": int(qa.get("selected_matches", len(value["matches"]))),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "method": value.get("method", {}),
    }


def build_manifest(assets: Mapping[str, Path]) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for source in SOURCE_LABELS:
        path = assets.get(source)
        if path is None:
            sources[source] = {
                "label": SOURCE_LABELS[source],
                "status": "processing",
                "matches_url": None,
            }
            continue
        destination_name = f"matches/{source}-matches.json.gz"
        sources[source] = source_record(source, path, destination_name)
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_utc": utc_now(),
        "catalogue_identity": "ERA5 LPS v5.6 physical-event ID",
        "sources": sources,
    }


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def publish(output: Path, assets: Mapping[str, Path]) -> Path:
    manifest = build_manifest(assets)
    for source, path in assets.items():
        destination = output / manifest["sources"][source]["matches_url"]
        atomic_copy(path, destination)
        if sha256(destination) != manifest["sources"][source]["sha256"]:
            raise RuntimeError(f"checksum mismatch after publishing {source}")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "manifest.json"
    temporary = destination.with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--merra2", type=Path)
    parser.add_argument("--imdaa", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    assets = {name: path for name, path in (("merra2", args.merra2), ("imdaa", args.imdaa)) if path}
    print(publish(args.output, assets))


if __name__ == "__main__":
    main()
