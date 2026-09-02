#!/usr/bin/env python3
"""Create resumable day chunks and a monthly MERRA-2 processing table."""

from __future__ import annotations

import argparse
import calendar
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path


FIRST_MERRA2_DAY = date(1980, 1, 1)


def parse_month(value: str) -> date:
    return datetime.strptime(value, "%Y-%m").date().replace(day=1)


def month_range(start: date, end: date) -> list[date]:
    output: list[date] = []
    current = start
    while current <= end:
        output.append(current)
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return output


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def plan(start: date, end: date, output: Path, chunk_size: int) -> dict[str, object]:
    if start < FIRST_MERRA2_DAY or end < start:
        raise ValueError("MERRA-2 range must begin no earlier than 1980-01 and end after it starts")
    months = month_range(start, end)
    first_day = max(FIRST_MERRA2_DAY, start - timedelta(days=1))
    end_days = calendar.monthrange(end.year, end.month)[1]
    boundary = end + timedelta(days=end_days)
    days = [first_day + timedelta(days=index) for index in range((boundary - first_day).days + 1)]
    output.mkdir(parents=True, exist_ok=True)
    atomic_text(
        output / "months.tsv",
        "".join(f"{index}\t{month:%Y%m}\n" for index, month in enumerate(months, 1)),
    )
    atomic_text(
        output / "days.tsv",
        "".join(f"{index}\t{day.isoformat()}\n" for index, day in enumerate(days, 1)),
    )
    chunks = output / "day-chunks"
    chunks.mkdir(parents=True, exist_ok=True)
    for stale in chunks.glob("days-*.tsv"):
        stale.unlink()
    chunk_paths: list[str] = []
    for offset in range(0, len(days), chunk_size):
        selected = days[offset : offset + chunk_size]
        path = chunks / f"days-{offset // chunk_size + 1:03d}.tsv"
        atomic_text(
            path,
            "".join(f"{index}\t{day.isoformat()}\n" for index, day in enumerate(selected, 1)),
        )
        chunk_paths.append(str(path))
    value: dict[str, object] = {
        "schema": "lps-atlas-merra2-backfill-plan-v1",
        "start_month": start.strftime("%Y-%m"),
        "end_month": end.strftime("%Y-%m"),
        "first_download_day": first_day.isoformat(),
        "last_download_day": boundary.isoformat(),
        "month_count": len(months),
        "day_count": len(days),
        "chunk_size": chunk_size,
        "chunks": chunk_paths,
    }
    atomic_text(output / "plan.json", json.dumps(value, indent=2, sort_keys=True) + "\n")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_month, default=parse_month("1980-01"))
    parser.add_argument("--end", type=parse_month, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=900)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.chunk_size <= 1_000:
        raise SystemExit("chunk size must be between 1 and 1000")
    print(json.dumps(plan(args.start, args.end, args.output, args.chunk_size), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
