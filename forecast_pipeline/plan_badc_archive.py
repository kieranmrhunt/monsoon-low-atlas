#!/usr/bin/env python3
"""Plan complete twice-daily Met Office cycles from the mounted BADC archive."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, utc_now
from .sources import BadcUkmoAdapter
from .update import read_manifest
from .versions import model_version


DEFAULT_START = datetime(2016, 3, 19, 0, tzinfo=UTC)
DEFAULT_END = datetime(2016, 12, 31, 12, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=BadcUkmoAdapter.DEFAULT_ROOT)
    parser.add_argument("--start", default=DEFAULT_START.strftime("%Y%m%d%H"))
    parser.add_argument("--end", default=DEFAULT_END.strftime("%Y%m%d%H"))
    parser.add_argument("--horizon", type=int, default=120)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    if start > end or start.hour not in {0, 12} or end.hour not in {0, 12}:
        raise ValueError("start/end must be ordered 00/12 UTC cycles")
    adapter = BadcUkmoAdapter(root=args.root)
    candidates = []
    missing_source_cycles = []
    cycle = start
    while cycle <= end:
        item = {
            "model": "ukmo-global",
            "cycle": cycle.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(cycle),
            "model_version": model_version("ukmo-global", cycle),
        }
        if adapter.cycle_complete(cycle, args.horizon):
            candidates.append(item)
        else:
            missing_source_cycles.append(item["cycle"])
        cycle += timedelta(hours=12)

    available: set[str] = set()
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}" for item in manifest.get("archive", [])
        }
    pending = [item for item in candidates if f"{item['model']}:{item['cycle']}" not in available]
    plan = {
        "schema": "mla-forecast-badc-archive-plan-v1",
        "manifest_key": "archive_backfill_badc_ukmo",
        "generated_utc": iso_z(utc_now()),
        "models": ["ukmo-global"],
        "providers": ["CEDA/BADC Met Office Global archive"],
        "source_start_utc": iso_z(start),
        "source_end_utc": iso_z(end),
        "selection_policy": "every complete 00 and 12 UTC Met Office Global initialization in the audited 2016 BADC GRIB interval",
        "cycle_payload_policy": "all 21 six-hourly valid times from +0 to +120 h and every track published by the frozen atlas detector/linker, including zero-disturbance cycles",
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
        "".join(f"{index}\t{item['model']}\t{item['cycle']}\n" for index, item in enumerate(pending, 1)),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(
        f"Audited {plan['candidate_cycles']} twice-daily cycles: "
        f"{len(candidates)} source-complete, {len(missing_source_cycles)} absent, {len(pending)} to build"
    )


if __name__ == "__main__":
    main()
