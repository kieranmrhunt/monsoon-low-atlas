#!/usr/bin/env python3
"""Plan independent daily MERRA-2 downloads for one standard month."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .merra2 import days_in_month


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--jobs", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    days = days_in_month(args.month, include_next_midnight=True)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.jobs.with_suffix(args.jobs.suffix + f".part-{os.getpid()}")
    temporary.write_text(
        "".join(f"{index}\t{day.isoformat()}\n" for index, day in enumerate(days, 1)),
        encoding="utf-8",
    )
    os.replace(temporary, args.jobs)
    print(f"Planned {len(days)} MERRA-2 daily subsets for {args.month}")


if __name__ == "__main__":
    main()
