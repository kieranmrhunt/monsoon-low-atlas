#!/usr/bin/env python3
"""Plan the 1981--2010 ERA5 common-grid resolution-control experiment."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from reanalysis_pipeline.common import sha256

from .standardise_era5 import DEFAULT_BADC_ROOT, DEFAULT_SOURCE_ROOT


SCHEMA = "lps-atlas-era5-common-grid-plan-v1"
SOURCE_LABEL = "ERA5-1deg-control"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_tsv(path: Path, rows: list[list[object]]) -> None:
    if not rows:
        raise ValueError(f"refusing to create empty task file {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)
    os.replace(temporary, path)


def months(start: str, end: str) -> list[str]:
    return [period.strftime("%Y%m") for period in pd.period_range(start, end, freq="M")]


def _verify_month(source_root: Path, month: str) -> None:
    period = pd.Period(month, freq="M")
    next_month = (period + 1).strftime("%Y%m")
    for path in (
        source_root / "3hourly_pl_SA" / f"{month}.nc",
        source_root / "3hourly_pl_SA" / f"{next_month}.nc",
        source_root / "hourly_sfc_SA" / f"{month}.nc",
        source_root / "hourly_precip_SA" / f"{month}.nc",
    ):
        if not path.is_file():
            raise FileNotFoundError(path)


def build_plan(
    run_root: Path,
    static_file: Path,
    *,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    badc_root: Path = DEFAULT_BADC_ROOT,
    core_start: str = "198101",
    core_end: str = "201012",
) -> Path:
    run_root = run_root.resolve()
    source_root = source_root.resolve()
    badc_root = badc_root.resolve()
    static_file = static_file.resolve()
    if not static_file.is_file():
        raise FileNotFoundError(static_file)
    core = months(core_start, core_end)
    standard = months(
        (pd.Period(core_start, freq="M") - 1).strftime("%Y%m"),
        (pd.Period(core_end, freq="M") + 1).strftime("%Y%m"),
    )
    for month in standard:
        _verify_month(source_root, month)

    period_root = run_root / "era5-1deg-control-historical-analysis-common-1deg"
    data_root = period_root / "data"
    tracking_root = period_root / "tracking"
    link_root = period_root / "parallel-link"
    run = {
        "activity": "reanalysis-control",
        "institution": "ECMWF",
        "source_id": SOURCE_LABEL,
        "experiment_id": "historical",
        "member_id": "analysis",
        "grid_label": "common-1deg",
    }
    period_plan = {
        "schema": SCHEMA,
        "run": run,
        "source_label": SOURCE_LABEL,
        "core_months": core,
        "core_start": core_start,
        "core_end": core_end,
        "native_core_start": core_start,
        "native_core_end": core_end,
        "analysis_core_start": pd.Period(core_start, freq="M").start_time.isoformat(),
        "analysis_core_end_exclusive": (pd.Period(core_end, freq="M") + 1).start_time.isoformat(),
        "time_axis": None,
        "standard_months": standard,
        "paths": {
            "root": str(period_root),
            "data": str(data_root),
            "tracking": str(tracking_root),
            "parallel_link": str(link_root),
            "physics": str(period_root / "physics"),
            "published": str(period_root / "published"),
        },
        "experiment_role": "ERA5-as-model spatial-resolution control",
    }
    _atomic_json(period_root / "period-plan.json", period_plan)
    _atomic_tsv(
        run_root / "standardise.tsv",
        [[index, month, data_root, source_root, badc_root, static_file, "estimate"] for index, month in enumerate(standard, start=1)],
    )
    _atomic_tsv(
        run_root / "detect.tsv",
        [[index, month, data_root, tracking_root, static_file] for index, month in enumerate(core, start=1)],
    )
    _atomic_tsv(run_root / "link.tsv", [[1, SOURCE_LABEL, tracking_root, link_root]])
    plan = run_root / "plan.json"
    _atomic_json(
        plan,
        {
            "schema": SCHEMA,
            "created_utc": utc_now(),
            "source_root": str(source_root),
            "badc_root": str(badc_root),
            "common_static": {"path": str(static_file), "sha256": sha256(static_file)},
            "periods": [period_plan],
            "tasks": {"standardise": len(standard), "detect": len(core), "link_periods": 1},
            "method": (
                "ERA5 pressure-level and surface fields sampled to the exact common 1-degree "
                "nodes; vorticity recomputed after spatial sampling; terrain-validity surface "
                "pressure estimated consistently from hourly MSLP and fixed ERA5 orography; "
                "frozen v5.6 detector, linker, event gate and v5.5.1 intensity classifier."
            ),
        },
    )
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--badc-root", type=Path, default=DEFAULT_BADC_ROOT)
    parser.add_argument("--core-start", default="198101")
    parser.add_argument("--core-end", default="201012")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        build_plan(
            args.run_root,
            args.static_file,
            source_root=args.source_root,
            badc_root=args.badc_root,
            core_start=args.core_start,
            core_end=args.core_end,
        )
    )


if __name__ == "__main__":
    main()
