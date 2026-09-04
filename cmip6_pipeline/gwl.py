#!/usr/bin/env python3
"""Plan model-specific CMIP6 track runs at published global warming levels."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .plan import PeriodPlan, build_plan
from .source import DEFAULT_ROOT, RunSpec
from .warming import DEFAULT_GWL_TABLE


SCHEMA = "lps-atlas-cmip6-gwl-plan-v1"
SCENARIO_START_YEAR = 2015


@dataclass(frozen=True)
class ModelSource:
    institution: str
    calendar: str = "proleptic_gregorian"


MODEL_SOURCES = {
    "HadGEM3-GC31-LL": ModelSource("MOHC", "360_day"),
    "MIROC6": ModelSource("MIROC", "gregorian"),
    "MPI-ESM1-2-HR": ModelSource("DKRZ"),
    "MPI-ESM1-2-LR": ModelSource("MPI-M"),
    "MRI-ESM2-0": ModelSource("MRI"),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_tsv(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerows(rows)
    os.replace(temporary, path)


def crossing_windows(
    table: dict[str, Any],
    scenario: str,
    levels: list[float],
    source_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for run in table.get("runs", []):
        source_id = str(run["source_id"])
        if source_ids is not None and source_id not in source_ids:
            continue
        values = run.get("scenarios", {}).get(scenario)
        for level in levels:
            key = str(int(level)) if float(level).is_integer() else str(level)
            central_year = values.get(key) if isinstance(values, dict) else None
            if central_year is None:
                selected.append(
                    {
                        "source_id": source_id,
                        "member_id": run["member_id"],
                        "scenario": scenario,
                        "level_c": float(level),
                        "status": "not-reached-or-unavailable",
                    }
                )
                continue
            start_year, end_year = int(central_year) - 9, int(central_year) + 10
            selected.append(
                {
                    "source_id": source_id,
                    "member_id": run["member_id"],
                    "scenario": scenario,
                    "level_c": float(level),
                    "central_year": int(central_year),
                    "start_year": start_year,
                    "end_year": end_year,
                    "status": (
                        "scenario-window"
                        if start_year >= SCENARIO_START_YEAR
                        else "requires-historical-scenario-stitch"
                    ),
                }
            )
    return selected


def plan_gwl_runs(
    output_root: Path,
    static_file: Path,
    *,
    scenario: str,
    levels: list[float],
    gwl_table: Path = DEFAULT_GWL_TABLE,
    badc_root: Path = DEFAULT_ROOT,
    source_ids: set[str] | None = None,
) -> Path:
    table = json.loads(gwl_table.read_text(encoding="utf-8"))
    if table.get("schema") != "lps-atlas-ipcc-ar6-gwl-crossings-v1":
        raise ValueError(f"unsupported GWL table: {gwl_table}")
    records = crossing_windows(table, scenario, levels, source_ids)
    runnable = []
    rows = [["run_id", "source_id", "member_id", "scenario", "level_c", "central_year", "run_root"]]
    for record in records:
        if record["status"] != "scenario-window":
            continue
        source_id = record["source_id"]
        source = MODEL_SOURCES.get(source_id)
        if source is None:
            record["status"] = "unsupported-source"
            continue
        level_label = str(record["level_c"]).replace(".", "p")
        run_id = f"{_slug(source_id)}-{scenario}-gwl{level_label}"
        run_root = output_root / run_id
        period = PeriodPlan(
            RunSpec(
                "ScenarioMIP",
                source.institution,
                source_id,
                scenario,
                record["member_id"],
                "gn",
            ),
            f"{record['start_year']}01",
            f"{record['end_year']}12",
            "full",
            source.calendar,
        )
        manifest = build_plan(
            run_root,
            [period],
            badc_root=badc_root,
            static_file=static_file,
        )
        record.update({"run_id": run_id, "run_root": str(run_root.resolve()), "plan": str(manifest)})
        runnable.append(record)
        rows.append(
            [
                run_id,
                source_id,
                record["member_id"],
                scenario,
                record["level_c"],
                record["central_year"],
                run_root.resolve(),
            ]
        )
    output_root.mkdir(parents=True, exist_ok=True)
    task_file = output_root / "runs.tsv"
    _atomic_tsv(task_file, rows)
    manifest = output_root / "manifest.json"
    _atomic_json(
        manifest,
        {
            "schema": SCHEMA,
            "generated_utc": utc_now(),
            "scenario": scenario,
            "levels_c": levels,
            "definition": table["definition"],
            "source": table["source"],
            "records": records,
            "runnable_runs": len(runnable),
            "task_file": str(task_file.resolve()),
            "method": (
                "Each runnable period is the published IPCC AR6 centred 20-year GWL window. "
                "The frozen atlas detector and linker use the same model/member fields and native calendar as the fixed-window runs."
            ),
        },
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--scenario", default="ssp245")
    parser.add_argument("--level", type=float, action="append", required=True)
    parser.add_argument("--source-id", action="append")
    parser.add_argument("--gwl-table", type=Path, default=DEFAULT_GWL_TABLE)
    parser.add_argument("--badc-root", type=Path, default=DEFAULT_ROOT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        plan_gwl_runs(
            args.output_root,
            args.static_file,
            scenario=args.scenario,
            levels=list(dict.fromkeys(args.level)),
            gwl_table=args.gwl_table,
            badc_root=args.badc_root,
            source_ids=set(args.source_id) if args.source_id else None,
        )
    )


if __name__ == "__main__":
    main()
