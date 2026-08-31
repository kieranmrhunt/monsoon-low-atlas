#!/usr/bin/env python3
"""Plan event-spanning historical IFS HRES cycles from WeatherBench 2."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import WeatherBenchHresAdapter
from .update import read_manifest
from .versions import model_version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-core", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--spacing-hours", type=int, default=48)
    return parser.parse_args()


def floor_twelve_hour_cycle(value: datetime) -> datetime:
    value = value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return value - timedelta(hours=value.hour % 12)


def main() -> None:
    args = parse_args()
    if args.spacing_hours < 12 or args.spacing_hours % 12:
        raise ValueError("spacing-hours must be a positive multiple of twelve")
    with gzip.open(args.atlas_core, "rt", encoding="utf-8") as stream:
        core = json.load(stream)
    fields = {name: index for index, name in enumerate(core["track_fields"])}
    cycles: set[datetime] = set()
    for row in core["tracks"]:
        track_start = datetime.fromtimestamp(int(row[fields["start_ms"]]) / 1000, tz=UTC)
        track_end = datetime.fromtimestamp(int(row[fields["end_ms"]]) / 1000, tz=UTC)
        if track_end < WeatherBenchHresAdapter.START or track_start > WeatherBenchHresAdapter.END:
            continue
        cycle = floor_twelve_hour_cycle(
            max(WeatherBenchHresAdapter.START, track_start - timedelta(hours=24))
        )
        while cycle <= min(track_end, WeatherBenchHresAdapter.END):
            cycles.add(cycle)
            cycle += timedelta(hours=args.spacing_hours)
    desired = [
        {
            "model": "ifs",
            "cycle": cycle.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(cycle),
            "first_step_hours": 0,
            "horizon_hours": WeatherBenchHresAdapter.HORIZON,
            "valid_time_count": WeatherBenchHresAdapter.HORIZON // 6 + 1,
            "model_version": model_version("tigge-ecmwf", cycle),
        }
        for cycle in sorted(cycles)
    ]
    available: dict[str, int] = {}
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}": manifest_entry_horizon_hours(item)
            for item in manifest.get("archive", [])
        }
    pending = [
        item
        for item in desired
        if available.get(f"ifs:{item['cycle']}", -1) < WeatherBenchHresAdapter.HORIZON
    ]
    plan = {
        "schema": "mla-forecast-weatherbench-plan-v1",
        "manifest_key": "weatherbench_hres_archive",
        "generated_utc": iso_z(utc_now()),
        "models": ["ifs"],
        "providers": ["WeatherBench 2 public IFS HRES archive"],
        "source_start_utc": iso_z(WeatherBenchHresAdapter.START),
        "source_end_utc": iso_z(WeatherBenchHresAdapter.END),
        "selection_policy": (
            f"initialization 24 h before each ERA5 event, then every {args.spacing_hours} h "
            "through its published lifetime; duplicate cycles collapsed"
        ),
        "cycle_payload_policy": (
            "all 41 six-hourly valid times from +0 to +240 h, ensemble-mean weather not "
            "applicable, and every track published by the frozen atlas detector/linker"
        ),
        "desired_cycles": len(desired),
        "cycles": desired,
        "pending_cycles": pending,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(
            f"{index}\tifs\t{item['cycle']}\t{WeatherBenchHresAdapter.HORIZON}\t0\n"
            for index, item in enumerate(pending, 1)
        ),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(f"Planned {len(desired)} WeatherBench HRES cycles; {len(pending)} remain to build")


if __name__ == "__main__":
    main()
