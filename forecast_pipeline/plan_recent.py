#!/usr/bin/env python3
"""Plan the rolling 72-hour weather-capable forecast-cycle window."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import available_forecast_steps
from .update import read_manifest


TWICE_DAILY_MODELS = frozenset({"graphcast-noaa", "graphcast-ifs-noaa"})


def recent_cycle_is_scheduled(model: str, cycle: datetime) -> bool:
    """Return whether an operational stream publishes the candidate cycle."""

    if model in TWICE_DAILY_MODELS:
        return cycle.hour in {0, 12}
    return cycle.hour in {0, 6, 12, 18}


def planned_recent_cycles(
    manifest: dict,
    hours: int,
) -> list[dict[str, object]]:
    """Build the expected rolling cycle inventory for every live model."""

    cycles: list[dict[str, object]] = []
    for model, latest in sorted(manifest.get("latest", {}).items()):
        newest = datetime.fromisoformat(str(latest["cycle_utc"]).replace("Z", "+00:00"))
        for offset in range(0, hours + 1, 6):
            cycle = newest - timedelta(hours=offset)
            if not recent_cycle_is_scheduled(model, cycle):
                continue
            cycles.append({
                "model": model,
                "cycle": cycle.strftime("%Y%m%d%H"),
                "cycle_utc": iso_z(cycle),
                "horizon_hours": available_forecast_steps(model, cycle)[-1],
            })
    return cycles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--exclude-model", action="append", default=[])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = read_manifest(args.manifest)
    excluded = set(args.exclude_model)
    cycles = [
        item for item in planned_recent_cycles(manifest, args.hours)
        if item["model"] not in excluded
    ]
    available = {}
    for model, entries in manifest.get("recent", {}).items():
        for item in entries:
            key = f"{model}:{item.get('cycle')}"
            available[key] = max(available.get(key, -1), manifest_entry_horizon_hours(item))
    for item in manifest.get("archive", []):
        key = f"{item.get('model')}:{item.get('cycle')}"
        available[key] = max(available.get(key, -1), manifest_entry_horizon_hours(item))
    pending = [
        item for item in cycles
        if available.get(f"{item['model']}:{item['cycle']}", -1) < int(item["horizon_hours"])
    ]
    plan = {
        "schema": "mla-forecast-recent-plan-v1",
        "generated_utc": iso_z(utc_now()),
        "window_hours": args.hours,
        "excluded_models": sorted(excluded),
        "selection_policy": f"every six-hourly initialization through the preceding {args.hours} hours, reusing complete operational-archive assets before requesting any missing provider/model lead axis",
        "cycles": cycles,
        "pending_cycles": pending,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(f"{index}\t{item['model']}\t{item['cycle']}\n" for index, item in enumerate(pending, 1)),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(f"Planned {len(cycles)} recent model-cycles; {len(pending)} remain to build")


if __name__ == "__main__":
    main()
