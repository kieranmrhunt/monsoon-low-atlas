#!/usr/bin/env python3
"""Build lightweight map-first sidecars for existing forecast payloads."""

from __future__ import annotations

import argparse
import gzip
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .forecast_core import (
    ManifestLock,
    atomic_write_json,
    atomic_write_json_gz,
    compact_track_payload,
    publish_client_manifests,
    track_sidecar_url,
)
from .update import read_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--collections",
        default="latest,recent,archive",
        help="comma-separated manifest collections: latest,recent,archive,tigge_archive",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def manifest_items(manifest: dict[str, Any], collections: set[str]) -> list[tuple[str, str, str]]:
    output: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    if "latest" in collections:
        for model, entry in manifest.get("latest", {}).items():
            key = (str(model), str(entry.get("cycle", "")), str(entry.get("url", "")))
            output[key] = (
                str(model), str(entry.get("cycle", "")), str(entry.get("url", ""))
            )
    if "recent" in collections:
        for model, entries in manifest.get("recent", {}).items():
            for entry in entries:
                key = (str(model), str(entry.get("cycle", "")), str(entry.get("url", "")))
                output[key] = (
                    str(model), str(entry.get("cycle", "")), str(entry.get("url", ""))
                )
    for collection in ("archive", "tigge_archive"):
        if collection not in collections:
            continue
        for entry in manifest.get(collection, []):
            model = str(entry.get("model", ""))
            cycle = str(entry.get("cycle", ""))
            key = (model, cycle, str(entry.get("url", "")))
            output[key] = key
    return [value for value in output.values() if value[2]]


def build_one(root: Path, item: tuple[str, str, str], overwrite: bool) -> tuple[str, str, str, str, list[str]]:
    model, cycle, relative = item
    source = root / relative
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    weather_fields = sorted(
        name
        for name, field in payload.get("weather", {}).items()
        if isinstance(field, dict) and "shape" in field and "data" in field
    )
    if not weather_fields:
        return model, cycle, relative, relative, []
    tracks_relative = track_sidecar_url(relative)
    destination = root / tracks_relative
    if overwrite or not destination.is_file():
        atomic_write_json_gz(destination, compact_track_payload(payload))
    return model, cycle, relative, tracks_relative, weather_fields


def update_entries(
    manifest: dict[str, Any],
    updates: dict[tuple[str, str, str], tuple[str, list[str]]],
) -> None:
    for model, entry in manifest.get("latest", {}).items():
        update = updates.get((str(model), str(entry.get("cycle", "")), str(entry.get("url", ""))))
        if update:
            entry["tracks_url"], entry["weather_fields"] = update
    for model, entries in manifest.get("recent", {}).items():
        for entry in entries:
            update = updates.get((str(model), str(entry.get("cycle", "")), str(entry.get("url", ""))))
            if update:
                entry["tracks_url"], entry["weather_fields"] = update
    for collection in ("archive", "tigge_archive"):
        for entry in manifest.get(collection, []):
            update = updates.get((str(entry.get("model", "")), str(entry.get("cycle", "")), str(entry.get("url", ""))))
            if update:
                entry["tracks_url"], entry["weather_fields"] = update


def main() -> None:
    args = parse_args()
    allowed = {"latest", "recent", "archive", "tigge_archive"}
    collections = {value.strip() for value in args.collections.split(",") if value.strip()}
    unknown = collections - allowed
    if unknown:
        raise ValueError("unknown collections: " + ", ".join(sorted(unknown)))
    manifest_path = args.root / "manifest.json"
    snapshot = read_manifest(manifest_path)
    items = manifest_items(snapshot, collections)
    updates: dict[tuple[str, str, str], tuple[str, list[str]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(build_one, args.root, item, args.overwrite): item
            for item in items
        }
        for future in as_completed(futures):
            model, cycle, relative, tracks_relative, weather_fields = future.result()
            # Empty weather is also a material correction: old archive entries
            # may retain a stale cycles/ sidecar URL after the duplicate live
            # payload is cleaned. Point those entries back to their compact
            # archive payload and stop advertising unavailable map fields.
            updates[(model, cycle, relative)] = (tracks_relative, weather_fields)
    with ManifestLock(args.root):
        manifest = read_manifest(manifest_path)
        update_entries(manifest, updates)
        atomic_write_json(manifest_path, manifest)
        publish_client_manifests(args.root, manifest)
    print(f"Published {len(updates)}/{len(items)} weather-bearing track sidecars")


if __name__ == "__main__":
    main()
