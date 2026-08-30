#!/usr/bin/env python3
"""Plan event-spanning historical forecast initializations for the atlas."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, utc_now
from .update import read_manifest
from .versions import model_version


GEFS_CONTROL_DIRECT_START = datetime(2017, 1, 1, 0, tzinfo=UTC)
GFS_DIRECT_START = datetime(2021, 2, 26, 0, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-core", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--spacing-hours", type=int, default=48)
    return parser.parse_args()


def floor_cycle(value: datetime) -> datetime:
    value = value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return value - timedelta(hours=value.hour % 6)


def main() -> None:
    args = parse_args()
    if args.spacing_hours < 6 or args.spacing_hours % 6:
        raise ValueError("spacing-hours must be a positive multiple of six")
    with gzip.open(args.atlas_core, "rt", encoding="utf-8") as stream:
        core = json.load(stream)
    fields = {name: index for index, name in enumerate(core["track_fields"])}
    coverage_end = datetime.fromisoformat(str(core["meta"]["coverage_end"]).replace("Z", "+00:00"))
    cycles: set[tuple[str, datetime]] = set()
    eligible_tracks = 0
    for row in core["tracks"]:
        start = datetime.fromtimestamp(int(row[fields["start_ms"]]) / 1000, tz=UTC)
        end = datetime.fromtimestamp(int(row[fields["end_ms"]]) / 1000, tz=UTC)
        if end < GEFS_CONTROL_DIRECT_START:
            continue
        eligible_tracks += 1
        cycle = floor_cycle(max(GEFS_CONTROL_DIRECT_START, start - timedelta(hours=24)))
        while cycle <= min(end, coverage_end):
            model = "gfs" if cycle >= GFS_DIRECT_START else "gefs-control"
            cycles.add((model, cycle))
            cycle += timedelta(hours=args.spacing_hours)
    desired = [
        {
            "model": model,
            "cycle": value.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(value),
            "model_version": model_version(model, value),
        }
        for model, value in sorted(cycles, key=lambda item: (item[1], item[0]))
    ]
    available: set[str] = set()
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}" for item in manifest.get("archive", [])
        }
    pending = [item for item in desired if f"{item['model']}:{item['cycle']}" not in available]
    plan = {
        "schema": "mla-forecast-archive-plan-v1",
        "generated_utc": iso_z(utc_now()),
        "models": ["gefs-control", "gfs"],
        "providers": ["NOAA Open Data GEFS archive", "NOAA Open Data GFS archive"],
        "provider_earliest_cycle_utc": iso_z(GEFS_CONTROL_DIRECT_START),
        "model_transition_utc": iso_z(GFS_DIRECT_START),
        "catalogue_version": str(core["meta"].get("catalogue_version", "v5.6")),
        "catalogue_coverage_end": iso_z(coverage_end),
        "selection_policy": f"initialization 24 h before each ERA5 event, then every {args.spacing_hours} h through its published lifetime; duplicate cycles collapsed; GEFS control through 25 February 2021 and deterministic GFS thereafter",
        "cycle_payload_policy": "all 21 six-hourly valid times from +0 to +120 h and every track published by the frozen atlas detector/linker, including zero-disturbance cycles",
        "eligible_catalogue_tracks": eligible_tracks,
        "desired_cycles": len(desired),
        "already_available_cycles": len(desired) - len(pending),
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
    print(f"Planned {len(desired)} cycles; {len(pending)} remain to build")


if __name__ == "__main__":
    main()
