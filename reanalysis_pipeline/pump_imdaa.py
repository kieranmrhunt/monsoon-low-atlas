#!/usr/bin/env python3
"""Advance the IMDAA RDS backfill while keeping a bounded remote queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .ncmrwf import (
    download_completed,
    plan_requests,
    read_ledger,
    refresh_status,
    submit_requests,
)


ACTIVE_STATUSES = {"pending", "queued", "running", "processing", "submitted", "created"}
SUCCESS_STATUSES = {"completed", "complete", "ready", "success", "succeeded"}


def is_canary(record: dict[str, Any]) -> bool:
    days = record.get("days")
    return isinstance(days, list) and 0 < len(days) < 20


def pump(ledger_path: Path, output_root: Path, *, maximum_active: int) -> dict[str, Any]:
    statuses = refresh_status(ledger_path)
    downloaded = download_completed(ledger_path, output_root, maximum=maximum_active)
    ledger = read_ledger(ledger_path)
    canaries = [record for record in ledger["requests"].values() if is_canary(record)]
    canary_ready = len(canaries) >= 2 and all(record.get("sha256") for record in canaries)
    result: dict[str, Any] = {
        "remote_statuses": statuses,
        "downloaded": downloaded,
        "canary_ready": canary_ready,
        "submitted": 0,
    }
    if not canary_ready:
        result["state"] = "waiting_for_canary"
        return result

    active = sum(
        str(record.get("status", "unknown")).lower() in ACTIVE_STATUSES
        for record in ledger["requests"].values()
    )
    available = max(0, maximum_active - active)
    if available:
        result["submitted"] = submit_requests(
            ledger_path,
            plan_requests(1979, 1, 2020, 12),
            maximum=available,
        )
    result["state"] = "backfill_active" if active or result["submitted"] else "requests_complete"
    result["active_before_submit"] = active
    result["queue_capacity"] = maximum_active
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--maximum-active", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.maximum_active < 1:
        raise ValueError("--maximum-active must be positive")
    print(json.dumps(pump(args.ledger, args.output_root, maximum_active=args.maximum_active), sort_keys=True))


if __name__ == "__main__":
    main()
