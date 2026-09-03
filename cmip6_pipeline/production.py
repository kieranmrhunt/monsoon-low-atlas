#!/usr/bin/env python3
"""Production orchestration for annual-block CMIP6 linking."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from reanalysis_pipeline.parallel_link import manifest_rows, merge, prepare, worker


def link_period(
    source_label: str,
    output_root: Path,
    run_root: Path,
    *,
    workers: int = 4,
) -> Path:
    manifest = prepare(source_label, output_root, run_root)
    rows = manifest_rows(run_root)
    if workers < 1:
        raise ValueError("workers must be positive")
    with ThreadPoolExecutor(max_workers=min(workers, len(rows))) as executor:
        futures = {
            executor.submit(worker, run_root, int(row["task_id"])): row["core_year"]
            for row in rows
        }
        for future in as_completed(futures):
            path = future.result()
            print(f"linked core year {futures[future]}: {path}", flush=True)
    return merge(source_label, output_root, run_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    linker = subparsers.add_parser("link-period")
    linker.add_argument("--source-label", required=True)
    linker.add_argument("--output-root", type=Path, required=True)
    linker.add_argument("--run-root", type=Path, required=True)
    linker.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = link_period(
        args.source_label,
        args.output_root,
        args.run_root,
        workers=args.workers,
    )
    print(path)


if __name__ == "__main__":
    main()
