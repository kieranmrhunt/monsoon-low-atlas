#!/usr/bin/env python3
"""Keep the ECDS TIGGE queue full without waiting on Slurm workers.

The ECDS request is submitted once and its job identifier is recorded.  A
separate recovery job polls successful receipts, downloads staged GRIB data,
and hands it to the normal forecast processor.  This process deliberately does
no remote waiting and normally exits in seconds.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .forecast_core import atomic_write_json, iso_z, parse_cycle
from .recover_tigge_jobs import (
    credentials,
    staged_cycle_complete,
)
from .sources import TiggeAdapter
from .update import read_manifest


SCHEMA = "mla-ecds-tigge-submit-state-v1"
ACTIVE_STATUSES = {"accepted", "running"}
TERMINAL_FAILURES = {"failed", "rejected"}


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _job_payload(client: Any) -> list[dict[str, Any]]:
    # Asking only for live jobs is both faster and, critically, does not call
    # get_receipt(): that method waits until an accepted request completes.
    payload = client.get_jobs(
        limit=100,
        sortby="-created",
        status=sorted(ACTIVE_STATUSES),
    ).json
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    return [
        dict(job)
        for job in jobs
        if str(job.get("processID", TiggeAdapter.DATASET)) == TiggeAdapter.DATASET
    ]


def _active_remote_cycles(
    jobs: list[dict[str, Any]],
    state: dict[str, Any],
) -> set[tuple[str, str]]:
    active_ids = {
        str(job.get("jobID", ""))
        for job in jobs
        if str(job.get("status", "")).lower() in ACTIVE_STATUSES
    }
    return {
        (str(record.get("model", "")), str(record.get("cycle", "")))
        for record in state.get("attempts", [])
        if str(record.get("job_id", "")) in active_ids
        and record.get("model")
        and record.get("cycle")
    }


def _ordered_cycles(plan_path: Path, plan: dict[str, Any], wanted_models: set[str]) -> list[dict[str, Any]]:
    cycles = [
        dict(item)
        for item in plan.get("cycles", [])
        if str(item.get("model", "")) in wanted_models
    ]
    queue_path = plan_path.parent / "jobs.tsv"
    if not queue_path.is_file():
        return cycles
    by_key = {(str(item["model"]), str(item["cycle"])): item for item in cycles}
    ordered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        key = (fields[1], fields[2])
        item = by_key.get(key)
        if item is not None and key not in seen:
            ordered.append(item)
            seen.add(key)
    ordered.extend(item for item in cycles if (str(item["model"]), str(item["cycle"])) not in seen)
    return ordered


def _public_cycles(public_root: Path) -> set[tuple[str, str]]:
    manifest_path = public_root / "manifest.json"
    if not manifest_path.is_file():
        return set()
    manifest = read_manifest(manifest_path)
    return {
        (str(item.get("model", "")), str(item.get("cycle", "")))
        for item in manifest.get("tigge_archive", [])
        if item.get("model") and item.get("cycle")
    }


def _successful_recovery_cycles(run_root: Path) -> set[tuple[str, str]]:
    inventory = _load_json(run_root / "ecds-recovery.json", {})
    return {
        (str(item.get("model", "")), str(item.get("cycle", "")))
        for item in inventory.get("records", [])
        if item.get("model") and item.get("cycle")
    }


def _raw_cache_exists(run_root: Path, key: tuple[str, str]) -> bool:
    model, cycle = key
    return (run_root / "raw-tigge" / model / cycle / "all.grib").is_file()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_attempts(state: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for record in state.get("attempts", []):
        key = (str(record.get("model", "")), str(record.get("cycle", "")))
        if all(key):
            output[key] = record
    return output


def _steps(item: dict[str, Any]) -> list[int]:
    first = int(item.get("first_step_hours", 0))
    last = int(item["horizon_hours"])
    if first < 0 or last < first or first % 6 or last % 6:
        raise ValueError(f"invalid TIGGE lead range for {item.get('model')} {item.get('cycle')}")
    values = list(range(first, last + 1, 6))
    expected = int(item.get("valid_time_count", len(values)))
    if len(values) != expected:
        raise ValueError(
            f"non-contiguous TIGGE lead range for {item.get('model')} {item.get('cycle')}: "
            f"planned {expected}, constructed {len(values)}"
        )
    return values


def pump(
    plan_path: Path,
    run_root: Path,
    public_root: Path,
    client: Any,
    *,
    state_path: Path | None = None,
    capacity: int = 20,
    max_submissions: int | None = None,
    retry_after_hours: float = 36.0,
    max_attempts_per_cycle: int = 6,
    initial_cursor: int = 0,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if capacity < 1:
        raise ValueError("capacity must be positive")
    if retry_after_hours <= 0:
        raise ValueError("retry-after-hours must be positive")
    if max_attempts_per_cycle < 1:
        raise ValueError("max-attempts-per-cycle must be positive")
    now = (now or datetime.now(UTC)).astimezone(UTC)
    run_root = run_root.resolve()
    state_path = (state_path or run_root / "ecds-submit-state.json").resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    wanted_models = {str(value) for value in plan.get("models", [])}
    if not wanted_models:
        raise ValueError(f"TIGGE plan has no models: {plan_path}")
    cycles = _ordered_cycles(plan_path, plan, wanted_models)
    if not cycles:
        raise ValueError(f"TIGGE plan has no cycles for selected models: {plan_path}")
    if initial_cursor < 0:
        raise ValueError("initial-cursor cannot be negative")
    state_exists = state_path.is_file()
    state = _load_json(
        state_path,
        {"schema": SCHEMA, "attempts": [], "events": []},
    )
    if state.get("schema") != SCHEMA:
        raise ValueError(f"unsupported TIGGE submission state: {state_path}")

    jobs = _job_payload(client)
    status_by_id = {
        str(job.get("jobID", "")): str(job.get("status", "")).lower()
        for job in jobs
        if job.get("jobID")
    }
    active_jobs = [
        job for job in jobs
        if str(job.get("status", "")).lower() in ACTIVE_STATUSES
    ]
    active_cycles = _active_remote_cycles(active_jobs, state)
    for attempt in state.get("attempts", []):
        status = status_by_id.get(str(attempt.get("job_id", "")))
        if status:
            attempt["status"] = status
            attempt["status_checked_utc"] = iso_z(now)

    public = _public_cycles(public_root)
    recovered = _successful_recovery_cycles(run_root)
    latest = _latest_attempts(state)
    attempt_counts: dict[tuple[str, str], int] = {}
    for attempt in state.get("attempts", []):
        key = (str(attempt.get("model", "")), str(attempt.get("cycle", "")))
        if all(key):
            attempt_counts[key] = attempt_counts.get(key, 0) + 1

    retry_after = timedelta(hours=retry_after_hours)
    candidates: list[dict[str, Any]] = []
    skipped = {
        "published": 0,
        "staged": 0,
        "raw_cache": 0,
        "remote_success": 0,
        "active": 0,
        "recent_submission": 0,
        "attempt_limit": 0,
    }
    cursor = int(state.get("cursor", initial_cursor if not state_exists else 0)) % len(cycles)
    ordered = list(enumerate(cycles[cursor:], cursor)) + list(enumerate(cycles[:cursor]))
    for plan_index, item in ordered:
        key = (str(item["model"]), str(item["cycle"]))
        if key in public:
            skipped["published"] += 1
            continue
        if staged_cycle_complete(run_root, *key):
            skipped["staged"] += 1
            continue
        if _raw_cache_exists(run_root, key):
            skipped["raw_cache"] += 1
            continue
        if key in recovered:
            skipped["remote_success"] += 1
            continue
        if key in active_cycles:
            skipped["active"] += 1
            continue
        previous = latest.get(key)
        if previous:
            status = str(previous.get("status", "")).lower()
            submitted = _parse_time(previous.get("submitted_utc"))
            if status not in TERMINAL_FAILURES and submitted and now - submitted < retry_after:
                skipped["recent_submission"] += 1
                continue
        if attempt_counts.get(key, 0) >= max_attempts_per_cycle:
            skipped["attempt_limit"] += 1
            continue
        candidate = dict(item)
        candidate["_plan_index"] = plan_index
        candidates.append(candidate)

    queued_jobs = [
        job for job in active_jobs
        if str(job.get("status", "")).lower() == "accepted"
    ]
    available_slots = max(0, capacity - len(queued_jobs))
    if max_submissions is not None:
        available_slots = min(available_slots, max(0, max_submissions))
    selected = candidates[:available_slots]
    submitted: list[dict[str, Any]] = []
    submission_errors: list[dict[str, str]] = []
    queue_full = False
    next_cursor = cursor
    if not dry_run:
        for item in selected:
            model, cycle = str(item["model"]), str(item["cycle"])
            adapter = TiggeAdapter(model, workers=1)
            request = adapter.ecds_request(parse_cycle(cycle), _steps(item))
            try:
                remote = client.submit(TiggeAdapter.DATASET, request)
            except Exception as error:
                submission_errors.append({"model": model, "cycle": cycle, "message": str(error)})
                if adapter._is_queue_limit_error(error):
                    queue_full = True
                    break
                next_cursor = (int(item["_plan_index"]) + 1) % len(cycles)
                continue
            record = {
                "model": model,
                "cycle": cycle,
                "job_id": str(remote.request_id),
                "submitted_utc": iso_z(now),
                "status": "accepted",
                "horizon_hours": int(item["horizon_hours"]),
                "first_step_hours": int(item.get("first_step_hours", 0)),
            }
            state.setdefault("attempts", []).append(record)
            submitted.append(record)
            next_cursor = (int(item["_plan_index"]) + 1) % len(cycles)

    summary = {
        "schema": SCHEMA,
        "generated_utc": iso_z(now),
        "plan": str(plan_path.resolve()),
        "plan_generated_utc": plan.get("generated_utc"),
        "planned_cycles": len(cycles),
        "public_cycles": len(public & {(str(item["model"]), str(item["cycle"])) for item in cycles}),
        "account_active_jobs": len(active_jobs),
        "active_selected_cycles": len(active_cycles),
        "account_queued_jobs": len(queued_jobs),
        "queue_capacity": capacity,
        "available_slots": available_slots,
        "eligible_cycles": len(candidates),
        "selected_for_submission": len(selected),
        "submitted": len(submitted),
        "queue_full": queue_full,
        "skipped": skipped,
        "cursor_start": cursor,
        "cursor_next": next_cursor,
        "submission_errors": submission_errors,
        "dry_run": dry_run,
    }
    state.update(
        {
            "schema": SCHEMA,
            "updated_utc": summary["generated_utc"],
            "plan": summary["plan"],
            "plan_generated_utc": summary["plan_generated_utc"],
            "cursor": next_cursor,
            "last_summary": summary,
        }
    )
    state.setdefault("events", []).append(summary)
    state["events"] = state["events"][-200:]
    if not dry_run:
        atomic_write_json(state_path, state)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--credentials", type=Path, default=Path.home() / ".cdsapirc")
    parser.add_argument("--capacity", type=int, default=20)
    parser.add_argument("--max-submissions", type=int)
    parser.add_argument("--retry-after-hours", type=float, default=36.0)
    parser.add_argument("--max-attempts-per-cycle", type=int, default=6)
    parser.add_argument("--initial-cursor", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        from ecmwf.datastores import Client
    except ImportError as error:
        raise RuntimeError("ecmwf-datastores-client is required for asynchronous TIGGE submission") from error
    client = Client(
        url=TiggeAdapter.ECDS_URL,
        key=credentials(args.credentials),
        progress=False,
        cleanup=False,
        log_callback=lambda *_args, **_kwargs: None,
        timeout=(15, 120),
        retry_after=5,
        maximum_tries=3,
    )
    result = pump(
        args.plan,
        args.run_root,
        args.public_root,
        client,
        state_path=args.state,
        capacity=args.capacity,
        max_submissions=args.max_submissions,
        retry_after_hours=args.retry_after_hours,
        max_attempts_per_cycle=args.max_attempts_per_cycle,
        initial_cursor=args.initial_cursor,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
