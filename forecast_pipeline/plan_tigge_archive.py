#!/usr/bin/env python3
"""Plan a separate historical ECMWF TIGGE ensemble collection."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, utc_now
from .update import read_manifest
from .versions import model_version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-core", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--cycles", help="comma-separated explicit YYYYMMDDHH cycles (QA/seeding)")
    parser.add_argument("--start", default="2006100100")
    parser.add_argument("--end", default="2016031812")
    parser.add_argument("--spacing-hours", type=int, default=48)
    return parser.parse_args()


def floor_twelve_hour_cycle(value: datetime) -> datetime:
    value = value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return value - timedelta(hours=value.hour % 12)


def main() -> None:
    args = parse_args()
    if args.spacing_hours < 12 or args.spacing_hours % 12:
        raise ValueError("spacing-hours must be a positive multiple of twelve")
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    cycles: set[datetime] = set()
    selection_policy = "explicit QA/seed cycles"
    if args.cycles:
        cycles.update(
            datetime.strptime(value.strip(), "%Y%m%d%H").replace(tzinfo=UTC)
            for value in args.cycles.split(",") if value.strip()
        )
    else:
        if not args.atlas_core:
            raise ValueError("--atlas-core is required unless --cycles is supplied")
        with gzip.open(args.atlas_core, "rt", encoding="utf-8") as stream:
            core = json.load(stream)
        fields = {name: index for index, name in enumerate(core["track_fields"])}
        for row in core["tracks"]:
            track_start = datetime.fromtimestamp(int(row[fields["start_ms"]]) / 1000, tz=UTC)
            track_end = datetime.fromtimestamp(int(row[fields["end_ms"]]) / 1000, tz=UTC)
            if track_end < start or track_start > end:
                continue
            cycle = floor_twelve_hour_cycle(max(start, track_start - timedelta(hours=24)))
            while cycle <= min(track_end, end):
                cycles.add(cycle)
                cycle += timedelta(hours=args.spacing_hours)
        selection_policy = (
            f"initialization 24 h before each ERA5 event, then every {args.spacing_hours} h through its "
            "published lifetime; duplicate cycles collapsed"
        )
    desired = [
        {
            "model": "tigge-ecmwf",
            "cycle": cycle.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(cycle),
            "model_version": model_version("tigge-ecmwf", cycle),
        }
        for cycle in sorted(cycles)
    ]
    available: set[str] = set()
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}" for item in manifest.get("tigge_archive", [])
        }
    pending = [item for item in desired if f"{item['model']}:{item['cycle']}" not in available]
    plan = {
        "schema": "mla-forecast-tigge-plan-v1",
        "manifest_key": "tigge_backfill",
        "generated_utc": iso_z(utc_now()),
        "models": ["tigge-ecmwf"],
        "providers": ["ECMWF ECDS TIGGE archive"],
        "source_archive_start_utc": "2006-10-01T00:00:00Z",
        "requested_start_utc": iso_z(min(cycles)) if cycles else None,
        "requested_end_utc": iso_z(max(cycles)) if cycles else None,
        "selection_policy": selection_policy,
        "cycle_payload_policy": "control plus all available perturbed members; all 21 six-hourly valid times from +0 to +120 h and every track published by the frozen atlas detector/linker",
        "desired_cycles": len(desired),
        "cycles": desired,
        "pending_cycles": pending,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(f"{index}\t{item['model']}\t{item['cycle']}\n" for index, item in enumerate(pending, 1)),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(f"Planned {len(desired)} TIGGE cycles; {len(pending)} remain to build")


if __name__ == "__main__":
    main()
