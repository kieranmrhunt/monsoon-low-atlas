#!/usr/bin/env python3
"""Plan complete twice-daily Met Office cycles from the mounted BADC archive."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import BadcUkmoAdapter, available_forecast_steps
from .update import read_manifest
from .versions import model_version


DEFAULT_START = datetime(2016, 3, 19, 0, tzinfo=UTC)


def latest_badc_cycle(now: datetime | None = None) -> datetime:
    """Return the latest nominal 00/12 UTC cycle that could be archived."""
    value = (now or utc_now()).astimezone(UTC)
    return value.replace(hour=12 if value.hour >= 12 else 0, minute=0, second=0, microsecond=0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=BadcUkmoAdapter.DEFAULT_ROOT)
    parser.add_argument("--start", default=DEFAULT_START.strftime("%Y%m%d%H"))
    parser.add_argument("--end", default=latest_badc_cycle().strftime("%Y%m%d%H"))
    parser.add_argument("--workers", type=int, default=16)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    if start > end or start.hour not in {0, 12} or end.hour not in {0, 12}:
        raise ValueError("start/end must be ordered 00/12 UTC cycles")
    adapter = BadcUkmoAdapter(root=args.root)
    horizon = available_forecast_steps("ukmo-global", start)[-1]
    if args.workers < 1:
        raise ValueError("workers must be positive")
    items = []
    cycle = start
    while cycle <= end:
        items.append((cycle, {
            "model": "ukmo-global",
            "cycle": cycle.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(cycle),
            "horizon_hours": horizon,
            "model_version": model_version("ukmo-global", cycle),
        }))
        cycle += timedelta(hours=12)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        completeness = executor.map(
            lambda value: adapter.cycle_complete(value[0], horizon),
            items,
        )
        audited = [(item, complete) for (_, item), complete in zip(items, completeness)]
    candidates = [item for item, complete in audited if complete]
    missing_source_cycles = [item["cycle"] for item, complete in audited if not complete]

    available: dict[str, int] = {}
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}": manifest_entry_horizon_hours(item)
            for item in manifest.get("archive", [])
        }
    pending = [
        item for item in candidates
        if available.get(f"{item['model']}:{item['cycle']}", -1) < int(item["horizon_hours"])
    ]
    plan = {
        "schema": "mla-forecast-badc-archive-plan-v1",
        "manifest_key": "archive_backfill_badc_ukmo",
        "generated_utc": iso_z(utc_now()),
        "models": ["ukmo-global"],
        "providers": ["CEDA/BADC Met Office Global archive"],
        "source_start_utc": iso_z(start),
        "source_end_utc": iso_z(end),
        "selection_policy": "every complete 00 and 12 UTC Met Office Global initialization in the requested BADC GRIB interval",
        "cycle_payload_policy": "all 25 six-hourly valid times from +0 to +144 h and every track published by the frozen atlas detector/linker, including zero-disturbance cycles",
        "candidate_cycles": int((end - start).total_seconds() // (12 * 3600)) + 1,
        "source_complete_cycles": len(candidates),
        "source_missing_cycle_count": len(missing_source_cycles),
        "source_missing_cycles": missing_source_cycles,
        "cycles": candidates,
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
        f"Audited {plan['candidate_cycles']} twice-daily cycles: "
        f"{len(candidates)} source-complete, {len(missing_source_cycles)} absent, {len(pending)} to build"
    )


if __name__ == "__main__":
    main()
