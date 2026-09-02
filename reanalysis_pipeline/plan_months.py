#!/usr/bin/env python3
"""Write an inclusive YYYYMM Slurm task table."""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta
from pathlib import Path


def month(value: str) -> date:
    return datetime.strptime(value, "%Y-%m").date().replace(day=1)


def months(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end month precedes start month")
    output = []
    current = start
    while current <= end:
        output.append(current)
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=month, required=True)
    parser.add_argument("--end", type=month, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = months(args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + f".part-{os.getpid()}")
    temporary.write_text("".join(f"{index}\t{value:%Y%m}\n" for index, value in enumerate(selected, 1)), encoding="utf-8")
    os.replace(temporary, args.output)
    print(f"Planned {len(selected)} months from {selected[0]:%Y-%m} through {selected[-1]:%Y-%m}")


if __name__ == "__main__":
    main()
