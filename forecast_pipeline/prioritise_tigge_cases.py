#!/usr/bin/env python3
"""Add availability-tested case studies to the existing resumable TIGGE queue.

Keeps the old job table and cycle order intact: queued Slurm array indices and
the background submit cursor must not be reassigned to different cycles.
"""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import timedelta
from itertools import zip_longest
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, parse_cycle, utc_now
from .plan_tigge_archive import selected_models
from .tigge_catalogue import TiggeAvailability, load_constraints
from .versions import model_version


def merge_cases(plan: dict, cases: list[dict], models: list[str], availability) -> dict:
    result = copy.deepcopy(plan)
    by_key = {(item["model"], item["cycle"]): item for item in result["cycles"]}
    case_queues = []
    summaries = []
    for case in cases:
        start, end, anchor = (parse_cycle(case[key]) for key in ("start", "end", "anchor"))
        if start > end or not start <= anchor <= end:
            raise ValueError(f"Invalid case interval: {case}")
        if any(value.hour not in (0, 12) for value in (start, end, anchor)):
            raise ValueError("TIGGE case cycles must be at 00 or 12 UTC")
        cycle = start
        dates = []
        while cycle <= end:
            dates.append(cycle)
            cycle += timedelta(hours=12)
        # Get the named dates online first, then work outwards. At equal
        # distance prefer forecasts issued before the anchor over later ones.
        dates.sort(key=lambda value: (abs(value - anchor), value))
        queues = []
        counts = Counter()
        for model in models:
            queue = []
            for cycle in dates:
                steps = availability.available_steps(model, cycle)
                if not steps:
                    continue
                key = model, cycle.strftime("%Y%m%d%H")
                if key not in by_key:
                    item = {
                        "model": model, "cycle": key[1], "cycle_utc": iso_z(cycle),
                        "first_step_hours": steps[0], "horizon_hours": steps[-1],
                        "valid_time_count": len(steps), "model_version": model_version(model, cycle),
                    }
                    result["cycles"].append(item)
                    by_key[key] = item
                item = by_key[key]
                labels = item.setdefault("priority_cases", [])
                if case["name"] not in labels:
                    labels.append(case["name"])
                queue.append(key)
                counts[model] += 1
            queues.append(queue)
        case_queues.append([key for group in zip_longest(*queues) for key in group if key])
        summaries.append({**case, "cycles_by_model": dict(counts), "model_cycles": sum(counts.values())})
    # Alternate cases and centres, so neither case monopolises the tape queue.
    ranked = []
    seen = set()
    for group in zip_longest(*case_queues):
        for key in group:
            if key and key not in seen:
                seen.add(key)
                ranked.append(key)
    for rank, key in enumerate(ranked):
        by_key[key]["priority_rank"] = rank
    used_models = {item["model"] for item in result["cycles"]}
    result["models"] = list(dict.fromkeys([*result["models"], *(model for model in models if model in used_models)]))
    result["priority_cases"] = summaries
    result["priority_policy"] = "Explicit cases first; alternating cases and centres, nearest anchor cycles first; existing backlog resumes afterwards."
    result["desired_cycles"] = len(result["cycles"])
    result["generated_utc"] = iso_z(utc_now())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--constraints", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True, help="NAME:START:END:ANCHOR, cycles YYYYMMDDHH")
    parser.add_argument("--models", default="all")
    args = parser.parse_args()
    cases = []
    for value in args.case:
        parts = value.split(":")
        if len(parts) != 4:
            parser.error("--case must be NAME:START:END:ANCHOR")
        cases.append(dict(zip(("name", "start", "end", "anchor"), parts)))
    plan = json.loads(args.plan.read_text())
    constraints, metadata = load_constraints(args.constraints)
    models = selected_models(args.models)
    preferred = ["tigge-imd", "tigge-ncmrwf", "tigge-ecmwf", "tigge-ukmo", "tigge-ncep"]
    models.sort(key=lambda model: (preferred.index(model) if model in preferred else len(preferred), model))
    result = merge_cases(plan, cases, models, TiggeAvailability(constraints))
    result["priority_availability_source"] = metadata
    if args.output.resolve() == args.plan.resolve():
        backup = args.plan.with_name(f"plan-before-case-priority-{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}.json")
        atomic_write_json(backup, plan)
    atomic_write_json(args.output, result)
    print(json.dumps({"plan": str(args.output), "previous_cycles": len(plan["cycles"]),
                      "total_cycles": len(result["cycles"]), "cases": result["priority_cases"]}, indent=2))


if __name__ == "__main__":
    main()
