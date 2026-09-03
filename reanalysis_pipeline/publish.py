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
from .native import NATIVE_INDEX_SCHEMA, NATIVE_MONTH_SCHEMA


MANIFEST_SCHEMA = "lps-atlas-reanalysis-manifest-v1"
SOURCE_LABELS = {
    "merra2": "MERRA-2",
    "imdaa": "IMDAA",
    "jra55": "JRA-55",
    "erainterim": "ERA-Interim",
}


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


def validate_native_archive(path: Path, source: str) -> dict[str, Any]:
    index_path = path / "index.json"
    value = json.loads(index_path.read_text(encoding="utf-8"))
    if value.get("schema") != NATIVE_INDEX_SCHEMA or str(value.get("source", "")).lower() != source:
        raise ValueError(f"{index_path} is not a {source} {NATIVE_INDEX_SCHEMA} asset")
    months = value.get("months")
    if not isinstance(months, dict) or not months:
        raise ValueError(f"{index_path} has no monthly source-native track inventory")
    selection = value.get("selection")
    if not isinstance(selection, dict) or selection.get("schema") != "lps-atlas-reanalysis-physical-selection-v1":
        raise ValueError(f"{index_path} does not document its physical-event selection")
    for month, record in months.items():
        if not (len(month) == 6 and month.isdigit()) or not isinstance(record, dict):
            raise ValueError(f"{index_path} has an invalid month record")
        month_path = path / str(record.get("url", ""))
        month_asset = read_gzip_json(month_path)
        if (
            month_asset.get("schema") != NATIVE_MONTH_SCHEMA
            or str(month_asset.get("source", "")).lower() != source
            or month_asset.get("month") != month
            or not isinstance(month_asset.get("tracks"), dict)
        ):
            raise ValueError(f"{month_path} is not a valid source-native month asset")
        if int(record.get("tracks", -1)) != len(month_asset["tracks"]):
            raise ValueError(f"{month_path} track count does not match its index")
        if int(record.get("bytes", -1)) != month_path.stat().st_size or record.get("sha256") != sha256(month_path):
            raise ValueError(f"{month_path} checksum metadata does not match")
    return value


def native_record(source: str, path: Path) -> dict[str, Any]:
    value = validate_native_archive(path, source)
    months = list(value["months"])
    return {
        "index_url": f"native/{source}/index.json",
        "url_template": f"native/{source}/{{month}}.json.gz",
        "start_month": months[0],
        "end_month": months[-1],
        "month_count": len(months),
        "source_tracks": int(value.get("track_count", 0)),
        "linker_tracks": int(value.get("linker_track_count", 0)),
        "source_track_points": int(value.get("point_count", 0)),
        "selection": value.get("selection"),
    }


def source_record(
    source: str,
    path: Path,
    destination_name: str,
    native_path: Path | None = None,
) -> dict[str, Any]:
    value = validate_match_asset(path, source)
    qa = value.get("qa", {})
    record = {
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
    if native_path is not None:
        record["native_tracks"] = native_record(source, native_path)
    return record


def build_manifest(
    assets: Mapping[str, Path],
    native_archives: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    native_archives = native_archives or {}
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
        sources[source] = source_record(source, path, destination_name, native_archives.get(source))
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


def install_native_archive(source: str, path: Path, output: Path) -> None:
    value = validate_native_archive(path, source)
    destination = output / "native" / source
    destination.mkdir(parents=True, exist_ok=True)
    for record in value["months"].values():
        atomic_copy(path / record["url"], destination / record["url"])
    # The index is the commit marker and is installed only after every month.
    atomic_copy(path / "index.json", destination / "index.json")


def existing_assets(output: Path, supplied: Mapping[str, Path]) -> dict[str, Path]:
    """Retain validated ready sources when publishing one completed backfill."""

    assets = dict(supplied)
    for source in SOURCE_LABELS:
        existing = output / "matches" / f"{source}-matches.json.gz"
        if source not in assets and existing.is_file():
            try:
                validate_match_asset(existing, source)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            assets[source] = existing
    return assets


def existing_native_archives(output: Path) -> dict[str, Path]:
    archives: dict[str, Path] = {}
    for source in SOURCE_LABELS:
        existing = output / "native" / source
        if not (existing / "index.json").is_file():
            continue
        try:
            validate_native_archive(existing, source)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        archives[source] = existing
    return archives


def publish(
    output: Path,
    assets: Mapping[str, Path],
    native_archives: Mapping[str, Path] | None = None,
) -> Path:
    # Install newly completed assets first, then rescan the publication
    # directory. If two source finalizers finish together, the later manifest
    # writer therefore observes the earlier source file instead of dropping it.
    for source, path in assets.items():
        validate_match_asset(path, source)
    for source, path in assets.items():
        destination = output / "matches" / f"{source}-matches.json.gz"
        atomic_copy(path, destination)
    for source, path in (native_archives or {}).items():
        install_native_archive(source, path, output)
    assets = existing_assets(output, {})
    native_archives = existing_native_archives(output)
    manifest = build_manifest(assets, native_archives)
    for source, path in assets.items():
        destination = output / manifest["sources"][source]["matches_url"]
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
    parser.add_argument("--jra55", type=Path)
    parser.add_argument("--erainterim", type=Path)
    for source in SOURCE_LABELS:
        parser.add_argument(f"--{source}-native", dest=f"{source}_native", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    assets = {
        name: getattr(args, name)
        for name in SOURCE_LABELS
        if getattr(args, name) is not None
    }
    native_archives = {
        name: getattr(args, f"{name}_native")
        for name in SOURCE_LABELS
        if getattr(args, f"{name}_native") is not None
    }
    print(publish(args.output, assets, native_archives))


if __name__ == "__main__":
    main()
