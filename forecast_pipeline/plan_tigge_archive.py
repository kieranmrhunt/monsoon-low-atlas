#!/usr/bin/env python3
"""Plan a separate, availability-aware multi-centre TIGGE collection."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import (
    TIGGE_CENTRES,
    TIGGE_MODEL_IDS,
    available_forecast_steps,
    tigge_archive_provider,
)
from .tigge_catalogue import TiggeAvailability, load_constraints
from .update import read_manifest
from .versions import model_version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-core", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--manifest-key", default="tigge_backfill")
    parser.add_argument("--cycles", help="comma-separated explicit YYYYMMDDHH cycles (QA/seeding)")
    parser.add_argument(
        "--models",
        default="tigge-ecmwf",
        help="comma-separated TIGGE model IDs; use 'all' for every contributing centre",
    )
    availability = parser.add_mutually_exclusive_group()
    availability.add_argument("--constraints", type=Path, help="pinned ECDS constraints JSON")
    availability.add_argument(
        "--fetch-constraints",
        action="store_true",
        help="discover and use the current ECDS constraints resource",
    )
    parser.add_argument(
        "--save-constraints",
        type=Path,
        help="write the fetched constraint rows for a reproducible companion plan",
    )
    parser.add_argument(
        "--one-per-model",
        choices=("earliest", "latest"),
        help="retain one availability-tested canary cycle per selected model",
    )
    parser.add_argument("--start", default="2006100100")
    parser.add_argument("--end", default="2016031812")
    parser.add_argument("--spacing-hours", type=int, default=48)
    parser.add_argument(
        "--job-order",
        choices=("chronological", "newest-first"),
        default="chronological",
        help="order pending work without changing the archive selection",
    )
    return parser.parse_args()


def floor_twelve_hour_cycle(value: datetime) -> datetime:
    value = value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return value - timedelta(hours=value.hour % 12)


def selected_models(value: str) -> list[str]:
    models = list(TIGGE_MODEL_IDS) if value.strip().lower() == "all" else [
        item.strip() for item in value.split(",") if item.strip()
    ]
    unknown = sorted(set(models) - set(TIGGE_CENTRES))
    if unknown:
        raise ValueError(f"unknown TIGGE model(s): {', '.join(unknown)}")
    if not models:
        raise ValueError("at least one TIGGE model is required")
    return models


def main() -> None:
    args = parse_args()
    if args.spacing_hours < 12 or args.spacing_hours % 12:
        raise ValueError("spacing-hours must be a positive multiple of twelve")
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    models = selected_models(args.models)
    constraint_rows = None
    constraint_metadata: dict[str, object] = {}
    availability: TiggeAvailability | None = None
    if args.fetch_constraints or args.constraints:
        constraint_rows, constraint_metadata = load_constraints(args.constraints)
        availability = TiggeAvailability(constraint_rows)
        if args.save_constraints:
            atomic_write_json(args.save_constraints, constraint_rows)
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
    desired = []
    unavailable = 0
    for model in models:
        centre = TIGGE_CENTRES[model]
        for cycle in sorted(cycles):
            if cycle < centre.archive_start:
                unavailable += 1
                continue
            steps = (
                availability.available_steps(model, cycle)
                if availability is not None
                else available_forecast_steps(model, cycle)
            )
            if not steps:
                unavailable += 1
                continue
            desired.append({
                "model": model,
                "cycle": cycle.strftime("%Y%m%d%H"),
                "cycle_utc": iso_z(cycle),
                "first_step_hours": int(steps[0]),
                "horizon_hours": int(steps[-1]),
                "valid_time_count": len(steps),
                "model_version": model_version(model, cycle),
            })
    if args.one_per_model:
        selected: dict[str, dict[str, object]] = {}
        for item in desired:
            previous = selected.get(str(item["model"]))
            if previous is None or (
                args.one_per_model == "latest"
                and str(item["cycle"]) > str(previous["cycle"])
            ) or (
                args.one_per_model == "earliest"
                and str(item["cycle"]) < str(previous["cycle"])
            ):
                selected[str(item["model"])] = item
        desired = list(selected.values())
        selection_policy += f"; {args.one_per_model} availability-tested cycle retained per centre as a canary"
    desired.sort(key=lambda item: (str(item["cycle"]), str(item["model"])))
    available: dict[str, tuple[int, int]] = {}
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        for item in manifest.get("tigge_archive", []):
            cycle_time = datetime.fromisoformat(str(item["cycle_utc"]).replace("Z", "+00:00"))
            valid_start = datetime.fromisoformat(
                str(item.get("valid_start_utc", item["cycle_utc"])).replace("Z", "+00:00")
            )
            first_step = int(round((valid_start - cycle_time).total_seconds() / 3600))
            available[f"{item.get('model')}:{item.get('cycle')}"] = (
                manifest_entry_horizon_hours(item), first_step
            )
    pending = [
        item for item in desired
        if (
            available.get(f"{item['model']}:{item['cycle']}", (-1, 999))[0]
            < int(item["horizon_hours"])
            or available.get(f"{item['model']}:{item['cycle']}", (-1, 999))[1]
            > int(item["first_step_hours"])
        )
    ]
    if args.job_order == "newest-first":
        # Stable sorts keep centres adjacent at shared initializations while
        # bringing the most relevant recent cycles online first.
        pending.sort(key=lambda item: str(item["model"]))
        pending.sort(key=lambda item: str(item["cycle"]), reverse=True)
    providers = sorted({
        tigge_archive_provider(
            str(item["model"]),
            datetime.strptime(str(item["cycle"]), "%Y%m%d%H").replace(tzinfo=UTC),
        )
        for item in desired
    })
    plan = {
        "schema": "mla-forecast-tigge-plan-v1",
        "manifest_key": args.manifest_key,
        "generated_utc": iso_z(utc_now()),
        "models": models,
        "providers": providers,
        "source_archive_start_utc": iso_z(min(TIGGE_CENTRES[model].archive_start for model in models)),
        "requested_start_utc": iso_z(min(cycles)) if cycles else None,
        "requested_end_utc": iso_z(max(cycles)) if cycles else None,
        "selection_policy": selection_policy,
        "cycle_payload_policy": (
            "all available control/perturbed members (perturbed-only for centres without a control); "
            "complete common six-hourly dynamical axis through each centre/cycle maximum and every "
            "track published by the frozen atlas detector/linker"
        ),
        "availability_policy": (
            "current ECDS machine-readable centre/date/forecast-type/field constraints"
            if availability is not None
            else "centre-level nominal maximum horizons"
        ),
        "job_order": args.job_order,
        "availability_source": constraint_metadata,
        "candidate_model_cycles": len(cycles) * len(models),
        "unavailable_model_cycles": unavailable,
        "desired_cycles": len(desired),
        "cycles": desired,
        "pending_cycles": pending,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(
            f"{index}\t{item['model']}\t{item['cycle']}\t{item['horizon_hours']}\t{item['first_step_hours']}\n"
            for index, item in enumerate(pending, 1)
        ),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(
        f"Planned {len(desired)} TIGGE model-cycles across {len(models)} centres; "
        f"{len(pending)} remain to build ({unavailable} unavailable candidates omitted)"
    )


if __name__ == "__main__":
    main()
