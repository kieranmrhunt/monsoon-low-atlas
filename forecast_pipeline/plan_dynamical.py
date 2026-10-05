"""Plan inventory-backed dynamical.org backfills and the daily GEFS window."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .dynamical import DynamicalAdapter
from .dynamical_reader import DATASETS, TRACKABLE_START
from .forecast_core import atomic_write_json, cycle_id, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import MODEL_DEFINITIONS, available_forecast_steps
from .update import read_manifest


def planned_cycles(inventories, start, end, manifest):
    available = {}
    for entry in manifest.get("archive", []):
        available[(entry.get("model"), entry.get("cycle"))] = entry
    rows, pending = [], []
    for model, inventory in inventories.items():
        for raw in inventory["cycles"]:
            cycle = datetime.fromisoformat(str(raw)[:19]).replace(tzinfo=UTC)
            if not start <= cycle <= end:
                continue
            if cycle_id(cycle) < TRACKABLE_START[model]:
                continue
            if model == "gefs-extended" and cycle.hour != 0:
                continue
            horizon = available_forecast_steps(model, cycle)[-1]
            if horizon not in inventory["steps"]:
                continue
            row = {"model": model, "cycle": cycle_id(cycle), "cycle_utc": iso_z(cycle),
                   "horizon_hours": horizon, "first_step_hours": 0}
            rows.append(row)
            old = available.get((model, row["cycle"]), {})
            if (manifest_entry_horizon_hours(old) < horizon
                    or int(old.get("members_available") or 0) < MODEL_DEFINITIONS[model].expected_members
                    or not {"precipitation", "vorticity"}.issubset(old.get("weather_fields", []))):
                pending.append(row)
    key = lambda row: (row["cycle"], row["model"])
    return sorted(rows, key=key), sorted(pending, key=key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("archive", "recent"), default="archive")
    parser.add_argument("--models", default="aifs,gefs-extended")
    parser.add_argument("--start", help="first initialization date, YYYY-MM-DD")
    parser.add_argument("--end", help="last initialization date, YYYY-MM-DD")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    args = parser.parse_args()
    models = args.models.split(",")
    if not models or any(model not in DATASETS for model in models):
        parser.error("models must be aifs and/or gefs-extended")
    if args.mode == "recent":
        if models != ["gefs-extended"]:
            parser.error("recent mode is for the daily GEFS extension; AIFS has its existing live updater")
        end, _ = DynamicalAdapter("gefs-extended").resolve_available_cycle("latest")
        start = end - timedelta(hours=72)
    else:
        if not args.start or not args.end:
            parser.error("archive mode requires --start and --end")
        start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
        end = datetime.fromisoformat(args.end).replace(tzinfo=UTC, hour=23, minute=59)
        if end < start:
            parser.error("end precedes start")
    inventories = {model: DynamicalAdapter(model).inventory() for model in models}
    rows, pending = planned_cycles(inventories, start, end, read_manifest(args.manifest))
    plan = {
        "schema": "mla-dynamical-plan-v1", "manifest_key": "dynamical_archive" if args.mode == "archive" else "dynamical_recent",
        "generated_utc": iso_z(utc_now()), "mode": args.mode, "models": models,
        "providers": ["dynamical.org STAC / original-provider GRIB"],
        "selection_policy": "all available initializations in the selected date interval; complete six-hourly lead axes and all members",
        "required_weather_fields": ["precipitation", "vorticity"],
        "start_utc": iso_z(start), "end_utc": iso_z(end),
        "required_input_starts": {model: TRACKABLE_START[model] for model in models},
        "cycles": rows, "pending_cycles": pending,
        "desired_cycles": len(rows), "already_available_cycles": len(rows) - len(pending),
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text("".join(f"{i}\t{row['model']}\t{row['cycle']}\t{row['horizon_hours']}\t0\n"
                                 for i, row in enumerate(pending, 1)), encoding="utf-8")
    print(f"Dynamical {args.mode}: {len(rows)} cycles, {len(pending)} pending")


if __name__ == "__main__":
    main()
