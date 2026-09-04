#!/usr/bin/env python3
"""Create deterministic, resumable manifests for paired CMIP6 windows."""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from reanalysis_pipeline.common import sha256

from .model_calendar import TimeAxis, native_stamp, time_axis
from .source import DEFAULT_ROOT, RunSpec, files_overlapping_stamps
from .standardise import FIELD_TABLES, field_table


SCHEMA = "lps-atlas-cmip6-production-plan-v1"


@dataclass(frozen=True)
class PeriodPlan:
    spec: RunSpec
    core_start: str
    core_end: str
    next_halo: str = "full"
    calendar: str = "proleptic_gregorian"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _months(start: str, end: str) -> list[str]:
    periods = pd.period_range(start=pd.Period(start, freq="M"), end=pd.Period(end, freq="M"), freq="M")
    return [period.strftime("%Y%m") for period in periods]


def _previous(month: str) -> str:
    return (pd.Period(month, freq="M") - 1).strftime("%Y%m")


def _next(month: str) -> str:
    return (pd.Period(month, freq="M") + 1).strftime("%Y%m")


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
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerows(rows)
    os.replace(temporary, path)


def _verify_source_month(
    root: Path,
    spec: RunSpec,
    month: str,
    variables: tuple[str, ...],
    axis: TimeAxis,
) -> None:
    start = pd.Period(month, freq="M").start_time
    end = (pd.Period(month, freq="M") + 1).start_time
    native_start, native_end = axis.native_bounds_for_analysis_interval(start, end)
    for variable in variables:
        directory = spec.field_directory(root, field_table(spec, variable), variable)
        files_overlapping_stamps(
            directory,
            native_stamp(native_start),
            native_stamp(native_end),
        )


def build_plan(
    run_root: Path,
    periods: list[PeriodPlan],
    *,
    badc_root: Path = DEFAULT_ROOT,
    static_file: Path,
) -> Path:
    run_root = run_root.resolve()
    static_file = static_file.resolve()
    if not static_file.is_file():
        raise FileNotFoundError(static_file)
    standard_rows: list[list[object]] = []
    boundary_rows: list[list[object]] = []
    detect_rows: list[list[object]] = []
    link_rows: list[list[object]] = []
    period_records: list[dict[str, Any]] = []

    for period_index, item in enumerate(periods, start=1):
        if item.next_halo not in {"full", "boundary"}:
            raise ValueError(f"unsupported next_halo mode {item.next_halo}")
        native_core = _months(item.core_start, item.core_end)
        if not native_core:
            raise ValueError(f"empty core interval {item.core_start}..{item.core_end}")
        label = item.spec.slug
        period_root = run_root / label
        data_root = period_root / "data"
        tracking_root = period_root / "tracking"
        link_root = period_root / "parallel-link"
        axis = time_axis(item.calendar, item.core_start)
        analysis_start, analysis_end = axis.analysis_interval_for_native_months(
            item.core_start,
            item.core_end,
        )
        core = _months(
            analysis_start.strftime("%Y%m"),
            (analysis_end - pd.Timedelta(hours=1)).strftime("%Y%m"),
        )
        axis_path = period_root / "time-axis.json"
        _atomic_json(axis_path, axis.record())
        full_standard_months = [_previous(core[0]), *core]
        if item.next_halo == "full":
            full_standard_months.append(_next(core[-1]))
        full_standard_months = list(dict.fromkeys(full_standard_months))

        for month in full_standard_months:
            _verify_source_month(badc_root, item.spec, month, tuple(FIELD_TABLES), axis)
            standard_rows.append(
                [
                    len(standard_rows) + 1,
                    item.spec.activity,
                    item.spec.institution,
                    item.spec.source_id,
                    item.spec.experiment_id,
                    item.spec.member_id,
                    item.spec.grid_label,
                    month,
                    data_root,
                    axis_path,
                ]
            )
        boundary_timestamp: str | None = None
        if item.next_halo == "boundary":
            boundary_month = _next(core[-1])
            boundary_timestamp = f"{boundary_month[:4]}-{boundary_month[4:]}-01T00:00:00"
            _verify_source_month(
                badc_root,
                item.spec,
                boundary_month,
                ("ua", "va", "ta", "hus"),
                axis,
            )
            boundary_rows.append(
                [
                    len(boundary_rows) + 1,
                    item.spec.activity,
                    item.spec.institution,
                    item.spec.source_id,
                    item.spec.experiment_id,
                    item.spec.member_id,
                    item.spec.grid_label,
                    boundary_timestamp,
                    data_root,
                    axis_path,
                ]
            )
        for month in core:
            detect_rows.append([len(detect_rows) + 1, month, data_root, tracking_root, static_file])
        link_rows.append([period_index, label, tracking_root, link_root])
        record = {
            "run": asdict(item.spec),
            "source_label": label,
            "core_months": core,
            "core_start": item.core_start,
            "core_end": item.core_end,
            "native_core_months": native_core,
            "native_core_start": item.core_start,
            "native_core_end": item.core_end,
            "analysis_core_start": analysis_start.isoformat(),
            "analysis_core_end_exclusive": analysis_end.isoformat(),
            "time_axis": {
                **axis.record(),
                "path": str(axis_path),
                "sha256": sha256(axis_path),
            },
            "standard_months": full_standard_months,
            "next_auxiliary_boundary": boundary_timestamp,
            "paths": {
                "root": str(period_root),
                "data": str(data_root),
                "tracking": str(tracking_root),
                "parallel_link": str(link_root),
                "physics": str(period_root / "physics"),
                "published": str(period_root / "published"),
            },
        }
        _atomic_json(period_root / "period-plan.json", {"schema": SCHEMA, **record})
        period_records.append(record)

    _atomic_tsv(run_root / "standardise.tsv", standard_rows)
    if boundary_rows:
        _atomic_tsv(run_root / "aux-boundary.tsv", boundary_rows)
    _atomic_tsv(run_root / "detect.tsv", detect_rows)
    _atomic_tsv(run_root / "link.tsv", link_rows)
    manifest = run_root / "plan.json"
    _atomic_json(
        manifest,
        {
            "schema": SCHEMA,
            "created_utc": utc_now(),
            "badc_root": str(badc_root.resolve()),
            "common_static": {"path": str(static_file), "sha256": sha256(static_file)},
            "periods": period_records,
            "tasks": {
                "standardise": len(standard_rows),
                "auxiliary_boundary": len(boundary_rows),
                "detect": len(detect_rows),
                "link_periods": len(link_rows),
            },
            "method": (
                "All native calendar months on an invertible continuous analysis clock; full "
                "previous-month field halo; full or single-frame next-month auxiliary halo; "
                "frozen v5.6 detector/linker; annual overlapping link blocks."
            ),
        },
    )
    return manifest


def mpi_paired_periods() -> list[PeriodPlan]:
    return [
        PeriodPlan(
            RunSpec("CMIP", "MPI-M", "MPI-ESM1-2-HR", "historical", "r1i1p1f1", "gn"),
            "198101",
            "201012",
            "full",
        ),
        PeriodPlan(
            RunSpec("ScenarioMIP", "DKRZ", "MPI-ESM1-2-HR", "ssp245", "r1i1p1f1", "gn"),
            "207101",
            "210012",
            "boundary",
        ),
    ]


def miroc6_paired_periods(*, canary: bool = False) -> list[PeriodPlan]:
    historical_start, historical_end = ("199006", "199009") if canary else ("198101", "201012")
    future_start, future_end = ("208006", "208009") if canary else ("207101", "210012")
    return [
        PeriodPlan(
            RunSpec("CMIP", "MIROC", "MIROC6", "historical", "r1i1p1f1", "gn"),
            historical_start,
            historical_end,
            "full",
        ),
        PeriodPlan(
            RunSpec("ScenarioMIP", "MIROC", "MIROC6", "ssp245", "r1i1p1f1", "gn"),
            future_start,
            future_end,
            "full" if canary else "boundary",
        ),
    ]


def mpi_lr_paired_periods(*, canary: bool = False) -> list[PeriodPlan]:
    historical_start, historical_end = ("199006", "199009") if canary else ("198101", "201012")
    future_start, future_end = ("208006", "208009") if canary else ("207101", "210012")
    return [
        PeriodPlan(
            RunSpec("CMIP", "MPI-M", "MPI-ESM1-2-LR", "historical", "r1i1p1f1", "gn"),
            historical_start,
            historical_end,
            "full",
        ),
        PeriodPlan(
            RunSpec("ScenarioMIP", "MPI-M", "MPI-ESM1-2-LR", "ssp245", "r1i1p1f1", "gn"),
            future_start,
            future_end,
            "full" if canary else "boundary",
        ),
    ]


def mri_paired_periods(*, canary: bool = False) -> list[PeriodPlan]:
    historical_start, historical_end = ("199006", "199009") if canary else ("198101", "201012")
    # MRI-ESM2-0 pressure-level output ends at 18 UTC 31 December 2100,
    # so 2101-01-01 00 UTC is unavailable for the required interpolation
    # boundary. Use the immediately preceding complete 30-year window.
    future_start, future_end = ("208006", "208009") if canary else ("207001", "209912")
    return [
        PeriodPlan(
            RunSpec("CMIP", "MRI", "MRI-ESM2-0", "historical", "r1i1p1f1", "gn"),
            historical_start,
            historical_end,
            "full",
        ),
        PeriodPlan(
            RunSpec("ScenarioMIP", "MRI", "MRI-ESM2-0", "ssp245", "r1i1p1f1", "gn"),
            future_start,
            future_end,
            "full",
        ),
    ]


def hadgem_ll_paired_periods(*, canary: bool = False) -> list[PeriodPlan]:
    historical_start, historical_end = ("199006", "199009") if canary else ("198101", "201012")
    future_start, future_end = ("208006", "208009") if canary else ("207001", "209912")
    return [
        PeriodPlan(
            RunSpec("CMIP", "MOHC", "HadGEM3-GC31-LL", "historical", "r1i1p1f3", "gn"),
            historical_start,
            historical_end,
            "full",
            "360_day",
        ),
        PeriodPlan(
            RunSpec(
                "ScenarioMIP",
                "MOHC",
                "HadGEM3-GC31-LL",
                "ssp245",
                "r1i1p1f3",
                "gn",
            ),
            future_start,
            future_end,
            "full",
            "360_day",
        ),
    ]


PRESETS = {
    "mpi-paired": mpi_paired_periods,
    "miroc6-paired": miroc6_paired_periods,
    "miroc6-canary": lambda: miroc6_paired_periods(canary=True),
    "mpi-lr-paired": mpi_lr_paired_periods,
    "mpi-lr-canary": lambda: mpi_lr_paired_periods(canary=True),
    "mri-paired": mri_paired_periods,
    "mri-canary": lambda: mri_paired_periods(canary=True),
    "hadgem-ll-paired": hadgem_ll_paired_periods,
    "hadgem-ll-canary": lambda: hadgem_ll_paired_periods(canary=True),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--badc-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--static-file", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for preset in PRESETS:
        subparsers.add_parser(preset)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = build_plan(
        args.run_root,
        PRESETS[args.command](),
        badc_root=args.badc_root,
        static_file=args.static_file,
    )
    print(path)


if __name__ == "__main__":
    main()
