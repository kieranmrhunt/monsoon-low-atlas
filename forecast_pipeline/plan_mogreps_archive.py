#!/usr/bin/env python3
"""Plan all complete cycles in the rolling Met Office MOGREPS-G archive."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import MogrepsAdapter, available_forecast_steps
from .update import read_manifest
from .versions import model_version


def latest_nominal_cycle(now: datetime | None = None) -> datetime:
    value = (now or utc_now()).astimezone(UTC)
    return value.replace(hour=(value.hour // 6) * 6, minute=0, second=0, microsecond=0)


def parse_args() -> argparse.Namespace:
    latest = latest_nominal_cycle()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--start", default=(latest - timedelta(days=28)).strftime("%Y%m%d%H"))
    parser.add_argument("--end", default=latest.strftime("%Y%m%d%H"))
    parser.add_argument("--workers", type=int, default=16)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    if start > end or start.hour % 6 or end.hour % 6:
        raise ValueError("start/end must be ordered six-hourly UTC cycles")
    if args.workers < 1:
        raise ValueError("workers must be positive")
    adapter = MogrepsAdapter(workers=args.workers)
    horizon = available_forecast_steps("mogreps-g", start)[-1]
    cycles = []
    cycle = start
    while cycle <= end:
        cycles.append(cycle)
        cycle += timedelta(hours=6)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        completeness = list(executor.map(lambda value: adapter.cycle_complete(value, horizon), cycles))

    complete = [
        {
            "model": "mogreps-g",
            "cycle": cycle.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(cycle),
            "horizon_hours": horizon,
            "model_version": model_version("mogreps-g", cycle),
        }
        for cycle, available in zip(cycles, completeness, strict=True)
        if available
    ]
    missing_source_cycles = [
        cycle.strftime("%Y%m%d%H")
        for cycle, available in zip(cycles, completeness, strict=True)
        if not available
    ]
    available: dict[str, int] = {}
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}": manifest_entry_horizon_hours(item)
            for item in manifest.get("archive", [])
        }
    pending = [
        item for item in complete
        if available.get(f"{item['model']}:{item['cycle']}", -1) < int(item["horizon_hours"])
    ]
    # Build the newest cycle first as the production canary, then work backwards
    # before the provider's oldest rolling objects expire.
    pending.sort(key=lambda item: str(item["cycle"]), reverse=True)
    plan = {
        "schema": "mla-forecast-mogreps-archive-plan-v1",
        "manifest_key": "archive_backfill_mogreps_g",
        "generated_utc": iso_z(utc_now()),
        "models": ["mogreps-g"],
        "providers": ["Met Office AWS Open Data"],
        "source_start_utc": iso_z(start),
        "source_end_utc": iso_z(end),
        "selection_policy": (
            "every complete 00/06/12/18 UTC initialization retained by the rolling "
            "Met Office Global Ensemble AWS archive"
        ),
        "cycle_payload_policy": (
            "all 42 six-hourly valid times from +0 to +246 h, all 18 members, and every "
            "track published by the frozen atlas detector/linker, including zero-disturbance cycles"
        ),
        "candidate_cycles": len(cycles),
        "source_complete_cycles": len(complete),
        "source_missing_cycle_count": len(missing_source_cycles),
        "source_missing_cycles": missing_source_cycles,
        "cycles": complete,
        "pending_cycles": pending,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(
            f"{index}\t{item['model']}\t{item['cycle']}\t{item['horizon_hours']}\n"
            for index, item in enumerate(pending, 1)
        ),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(
        f"Audited {len(cycles)} MOGREPS-G cycles: {len(complete)} source-complete, "
        f"{len(missing_source_cycles)} unavailable, {len(pending)} to build"
    )


if __name__ == "__main__":
    main()
