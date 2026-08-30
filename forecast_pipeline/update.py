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
from datetime import datetime
from pathlib import Path
from typing import Any

from .analysis_history import analysis_entry, replace_analysis_entry
from .archive import AtlasVerifier, archive_manifest_entry, archive_payload
from .forecast_core import atomic_write_json, atomic_write_json_gz, cycle_id, iso_z, utc_now
from .sources import DEFAULT_MODELS, MODEL_DEFINITIONS, adapter_for


LOGGER = logging.getLogger("mla.forecast.update")
NCEI_ARCHIVE_ROOT = "https://www.ncei.noaa.gov/oa/prod-model"
NOAA_AWS_ARCHIVE_ROOT = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
RECENT_WINDOW_HOURS = 48


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--atlas-core", type=Path, required=True)
    parser.add_argument("--cycle", default="latest", help="latest or YYYYMMDDHH")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument(
        "--horizon",
        default="available",
        help="provider maximum available lead, or an explicit multiple of six",
    )
    parser.add_argument("--members", type=int, help="development-only member cap")
    parser.add_argument("--workers", type=int, default=20)
    archive_group = parser.add_mutually_exclusive_group()
    archive_group.add_argument("--ncei-archive", action="store_true", help="read an explicit GFS cycle from NCEI")
    archive_group.add_argument("--noaa-aws-archive", action="store_true", help="read an explicit GFS cycle from NOAA's public AWS archive")
    parser.add_argument("--archive-only", action="store_true", help="seed archive without replacing Latest")
    parser.add_argument(
        "--archive-collection",
        choices=("archive", "tigge"),
        default="archive",
        help="write cycles to the operational archive or the separate TIGGE collection",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "mla-forecast-manifest-v1",
            "latest": {},
            "recent": {},
            "analysis_history": {},
            "archive": [],
            "tigge_archive": [],
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
        "model_version": payload.get("model_version", {}),
    }


def replace_archive_entry(entries: list[dict[str, Any]], new: dict[str, Any]) -> list[dict[str, Any]]:
    filtered = [
        item for item in entries
        if not (item.get("model") == new["model"] and item.get("cycle") == new["cycle"])
    ]
    filtered.append(new)
    return sorted(filtered, key=lambda item: (str(item.get("cycle", "")), str(item.get("model", ""))), reverse=True)


def replace_recent_entry(entries: list[dict[str, Any]], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain full weather-capable cycles within 48 hours of the newest run."""

    combined = [item for item in entries if str(item.get("cycle", "")) != str(new["cycle"])] + [new]
    combined.sort(key=lambda item: str(item.get("cycle", "")), reverse=True)
    newest = datetime.fromisoformat(str(combined[0]["cycle_utc"]).replace("Z", "+00:00"))
    retained = []
    for item in combined:
        cycle = datetime.fromisoformat(str(item["cycle_utc"]).replace("Z", "+00:00"))
        if (newest - cycle).total_seconds() <= RECENT_WINDOW_HOURS * 3600:
            retained.append(item)
    return retained


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )
    horizon_mode = str(args.horizon).strip().lower()
    explicit_horizon: int | None = None
    if horizon_mode != "available":
        explicit_horizon = int(horizon_mode)
        if explicit_horizon < 24 or explicit_horizon % 6:
            raise ValueError("horizon must be 'available' or a multiple of six of at least 24 hours")
    requested_models = model_ids(args.models)
    if (args.ncei_archive or args.noaa_aws_archive) and requested_models != ["gfs"]:
        raise ValueError("the public NOAA archive routes currently support only --models gfs")
    if (args.ncei_archive or args.noaa_aws_archive) and args.cycle == "latest":
        raise ValueError("a NOAA archive route requires an explicit --cycle")
    if args.archive_collection == "tigge" and not args.archive_only:
        raise ValueError("the TIGGE collection is historical and requires --archive-only")

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
    successful_horizons: dict[str, int] = {}
    for model in requested_models:
        definition = MODEL_DEFINITIONS[model]
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
            if explicit_horizon is None:
                resolved_cycle, steps = adapter.resolve_available_cycle(args.cycle)
                build_cycle = cycle_id(resolved_cycle)
            else:
                steps = list(range(0, explicit_horizon + 1, 6))
                build_cycle = args.cycle
            LOGGER.info(
                "Building %s cycle=%s resolved=%s horizon=+%d h",
                definition.label,
                args.cycle,
                build_cycle,
                int(steps[-1]),
            )
            payload = adapter.build(build_cycle, steps, member_limit=args.members)
            cycle = str(payload["cycle"])
            cycle_relative = f"cycles/{model}/{cycle}.json.gz"
            archive_relative = (
                f"tigge/{model}/{cycle}.json.gz"
                if args.archive_collection == "tigge"
                else f"archive/{model}/{cycle}.json.gz"
            )
            atomic_write_json_gz(args.output_root / cycle_relative, payload)
            archived = archive_payload(
                payload,
                verifier,
                include_weather=args.archive_collection != "tigge",
            )
            atomic_write_json_gz(args.output_root / archive_relative, archived)
            if not args.archive_only:
                current_entry = latest_entry(payload, cycle_relative)
                previous = manifest.setdefault("latest", {}).get(model)
                if previous is None or str(previous.get("cycle", "")) <= cycle:
                    manifest["latest"][model] = current_entry
                recent = manifest.setdefault("recent", {}).setdefault(model, [])
                manifest["recent"][model] = replace_recent_entry(recent, current_entry)
                history = manifest.setdefault("analysis_history", {}).setdefault(model, [])
                manifest["analysis_history"][model] = replace_analysis_entry(
                    history, analysis_entry(payload)
                )
            collection_key = "tigge_archive" if args.archive_collection == "tigge" else "archive"
            manifest[collection_key] = replace_archive_entry(
                manifest.setdefault(collection_key, []),
                archive_manifest_entry(archived, archive_relative),
            )
            manifest.setdefault("attempts", {})[model] = {
                "status": "success",
                "attempted_utc": iso_z(utc_now()),
                "cycle": cycle,
                "message": "cycle assets and public archive written",
            }
            successes += 1
            successful_horizons[model] = int(payload["horizon_hours"])
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
        "forecast_horizon_hours": (
            max(successful_horizons.values())
            if successful_horizons
            else explicit_horizon
        ),
        "forecast_horizons_hours": successful_horizons,
        "forecast_horizon_policy": (
            "complete provider/model lead axis"
            if explicit_horizon is None
            else f"explicit +{explicit_horizon} h"
        ),
        "weather_archive_policy": "latest, rolling 48-hour cycles and the operational archive include ensemble-mean vorticity and trailing-24-hour precipitation; TIGGE omits weather; all public archives omit internal tracking QA",
        "analysis_stitch_policy": "displayed history uses continuity-matched t+0 centres from the same model and operational version; live history retains 14 days and archive history uses the processed archive cadence",
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
        # Keep a rolling 48-hour comparison window in the full-cycle namespace.
        # Long-term operational searches retain weather in their archive assets,
        # so discard older duplicate cycle bundles only after the manifest is safe.
        for model, entry in manifest.get("latest", {}).items():
            cycle_dir = args.output_root / "cycles" / model
            keep = {
                cycle_dir / Path(str(item.get("url", ""))).name
                for item in manifest.get("recent", {}).get(model, [])
            }
            keep.add(cycle_dir / Path(str(entry.get("url", ""))).name)
            if not cycle_dir.is_dir():
                continue
            for candidate in cycle_dir.glob("*.json.gz"):
                if candidate not in keep:
                    candidate.unlink()
    LOGGER.info("Manifest written to %s (%d/%d models succeeded)", manifest_path, successes, len(requested_models))
    return 0 if successes else 1


if __name__ == "__main__":
    sys.exit(main())
