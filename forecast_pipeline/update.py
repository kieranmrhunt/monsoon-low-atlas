#!/usr/bin/env python3
"""Build live and searchable archived forecast assets for the atlas."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .archive import AtlasVerifier, archive_manifest_entry, archive_payload
from .forecast_core import atomic_write_json, atomic_write_json_gz, iso_z, utc_now
from .sources import DEFAULT_MODELS, MODEL_DEFINITIONS, adapter_for


LOGGER = logging.getLogger("mla.forecast.update")
NCEI_ARCHIVE_ROOT = "https://www.ncei.noaa.gov/oa/prod-model"
NOAA_AWS_ARCHIVE_ROOT = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--atlas-core", type=Path, required=True)
    parser.add_argument("--cycle", default="latest", help="latest or YYYYMMDDHH")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--horizon", type=int, default=120)
    parser.add_argument("--members", type=int, help="development-only member cap")
    parser.add_argument("--workers", type=int, default=20)
    archive_group = parser.add_mutually_exclusive_group()
    archive_group.add_argument("--ncei-archive", action="store_true", help="read an explicit GFS cycle from NCEI")
    archive_group.add_argument("--noaa-aws-archive", action="store_true", help="read an explicit GFS cycle from NOAA's public AWS archive")
    parser.add_argument("--archive-only", action="store_true", help="seed archive without replacing Latest")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "mla-forecast-manifest-v1",
            "latest": {},
            "archive": [],
            "attempts": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "mla-forecast-manifest-v1":
        raise ValueError(f"unsupported forecast manifest schema in {path}")
    return payload


def model_ids(value: str) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(values) - set(MODEL_DEFINITIONS))
    if unknown:
        raise ValueError(f"unknown model(s): {', '.join(unknown)}")
    return values


def latest_entry(payload: dict[str, Any], relative_url: str) -> dict[str, Any]:
    return {
        "cycle": payload["cycle"],
        "cycle_utc": payload["cycle_utc"],
        "generated_utc": payload["generated_utc"],
        "valid_end_utc": payload["valid_times"][-1],
        "url": relative_url,
        "forecast_tracks": len(payload.get("tracks", [])),
        "forecast_systems": len(payload.get("systems", [])),
        "members_available": payload["members"]["available"],
        "members_expected": payload["members"]["expected"],
        "qa_status": payload["qa"]["status"],
    }


def replace_archive_entry(entries: list[dict[str, Any]], new: dict[str, Any]) -> list[dict[str, Any]]:
    filtered = [
        item for item in entries
        if not (item.get("model") == new["model"] and item.get("cycle") == new["cycle"])
    ]
    filtered.append(new)
    return sorted(filtered, key=lambda item: (str(item.get("cycle", "")), str(item.get("model", ""))), reverse=True)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )
    if args.horizon < 24 or args.horizon % 6:
        raise ValueError("horizon must be a multiple of six and at least 24 hours")
    steps = list(range(0, args.horizon + 1, 6))
    requested_models = model_ids(args.models)
    if (args.ncei_archive or args.noaa_aws_archive) and requested_models != ["gfs"]:
        raise ValueError("the public NOAA archive routes currently support only --models gfs")
    if (args.ncei_archive or args.noaa_aws_archive) and args.cycle == "latest":
        raise ValueError("a NOAA archive route requires an explicit --cycle")

    args.output_root.mkdir(parents=True, exist_ok=True)
    lock_path = args.output_root / ".update.lock"
    lock_stream = lock_path.open("a+")
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        LOGGER.warning("Another forecast updater holds %s; exiting without overlap", lock_path)
        return 0

    manifest_path = args.output_root / "manifest.json"
    manifest = read_manifest(manifest_path)
    verifier = AtlasVerifier(args.atlas_core)
    started = utc_now()
    successes = 0
    for model in requested_models:
        definition = MODEL_DEFINITIONS[model]
        LOGGER.info("Building %s cycle=%s horizon=+%d h", definition.label, args.cycle, args.horizon)
        try:
            adapter = adapter_for(
                model,
                workers=args.workers,
                archive_root=(
                    NCEI_ARCHIVE_ROOT
                    if args.ncei_archive
                    else NOAA_AWS_ARCHIVE_ROOT if args.noaa_aws_archive else None
                ),
            )
            payload = adapter.build(args.cycle, steps, member_limit=args.members)
            cycle = str(payload["cycle"])
            cycle_relative = f"cycles/{model}/{cycle}.json.gz"
            archive_relative = f"archive/{model}/{cycle}.json.gz"
            atomic_write_json_gz(args.output_root / cycle_relative, payload)
            archived = archive_payload(payload, verifier)
            atomic_write_json_gz(args.output_root / archive_relative, archived)
            if not args.archive_only:
                previous = manifest.setdefault("latest", {}).get(model)
                if previous is None or str(previous.get("cycle", "")) <= cycle:
                    manifest["latest"][model] = latest_entry(payload, cycle_relative)
            manifest["archive"] = replace_archive_entry(
                manifest.setdefault("archive", []),
                archive_manifest_entry(archived, archive_relative),
            )
            manifest.setdefault("attempts", {})[model] = {
                "status": "success",
                "attempted_utc": iso_z(utc_now()),
                "cycle": cycle,
                "message": "cycle assets and compact archive written",
            }
            successes += 1
            LOGGER.info(
                "%s %s complete: tracks=%d systems=%d members=%d/%d verification=%s",
                definition.label,
                cycle,
                len(payload.get("tracks", [])),
                len(payload.get("systems", [])),
                payload["members"]["available"],
                payload["members"]["expected"],
                archived["verification"]["status"],
            )
        except Exception as error:
            LOGGER.exception("%s update failed", definition.label)
            manifest.setdefault("attempts", {})[model] = {
                "status": "failed",
                "attempted_utc": iso_z(utc_now()),
                "cycle_requested": args.cycle,
                "message": str(error)[:1000],
            }

    manifest.update({
        "schema": "mla-forecast-manifest-v1",
        "generated_utc": iso_z(utc_now()),
        "run_started_utc": iso_z(started),
        "schedule": "six-hourly",
        "forecast_horizon_hours": args.horizon,
        "weather_archive_policy": "latest cycle files include grids; searchable archive files retain tracks and ERA5 verification only",
        "catalogue_verification": {
            "version": verifier.catalogue_version,
            "coverage_start": verifier.coverage_start,
            "coverage_end": verifier.coverage_end,
        },
        "models": [asdict(MODEL_DEFINITIONS[model]) for model in DEFAULT_MODELS],
        "source_notes": {
            "icon": "Adapter reserved. Public ICON-EPS currently lacks the pressure-level member winds required for like-for-like v5.6 tracking, so it is not advertised as equivalent guidance.",
            "ensemble_weather": "Weather layers show the arithmetic member mean; map tracks retain individual members.",
        },
        "run": {
            "requested_models": requested_models,
            "successful_models": successes,
            "archive_source": (
                "NOAA NCEI"
                if args.ncei_archive
                else "NOAA public AWS archive" if args.noaa_aws_archive else "live provider feeds"
            ),
            "development_member_cap": args.members,
        },
    })
    atomic_write_json(manifest_path, manifest)
    if not args.archive_only:
        # Weather grids are intentionally a latest-cycle product. Historical
        # searches use the compact archive payloads, so discard superseded grid
        # bundles only after the manifest safely points at their replacement.
        for model, entry in manifest.get("latest", {}).items():
            cycle_dir = args.output_root / "cycles" / model
            keep = cycle_dir / Path(str(entry.get("url", ""))).name
            if not cycle_dir.is_dir():
                continue
            for candidate in cycle_dir.glob("*.json.gz"):
                if candidate != keep:
                    candidate.unlink()
    LOGGER.info("Manifest written to %s (%d/%d models succeeded)", manifest_path, successes, len(requested_models))
    return 0 if successes else 1


if __name__ == "__main__":
    sys.exit(main())
