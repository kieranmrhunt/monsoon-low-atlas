#!/usr/bin/env python3
"""Plan the rolling 48-hour weather-capable forecast-cycle window."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, utc_now
from .update import read_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=48)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = read_manifest(args.manifest)
    cycles = []
    for model, latest in sorted(manifest.get("latest", {}).items()):
        newest = datetime.fromisoformat(str(latest["cycle_utc"]).replace("Z", "+00:00"))
        for offset in range(0, args.hours + 1, 6):
            cycle = newest - timedelta(hours=offset)
            cycles.append({"model": model, "cycle": cycle.strftime("%Y%m%d%H"), "cycle_utc": iso_z(cycle)})
    available = {
        f"{model}:{item.get('cycle')}"
        for model, entries in manifest.get("recent", {}).items()
        for item in entries
    }
    pending = [item for item in cycles if f"{item['model']}:{item['cycle']}" not in available]
    plan = {
        "schema": "mla-forecast-recent-plan-v1",
        "generated_utc": iso_z(utc_now()),
        "window_hours": args.hours,
        "selection_policy": "every six-hourly initialization through the preceding 48 hours, relative to each model's latest complete +120 h cycle",
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
