#!/usr/bin/env python3
"""Recover successful ECDS TIGGE jobs whose local workers timed out.

ECDS retrieval jobs outlive the Slurm process that submitted them.  This tool
maps successful remote jobs back to TIGGE model cycles, downloads unpublished
GRIB results into the normal raw cache, and writes a job table that the existing
forecast processor can consume without making another ECDS request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import requests

from .sources import TIGGE_CENTRES


ECDS_URL = "https://ecds.ecmwf.int/api"
JOBS_URL = f"{ECDS_URL}/retrieve/v1/jobs"
ORIGIN_MODELS = {
    centre.archive_origin: model for model, centre in TIGGE_CENTRES.items()
}


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def credentials(path: Path) -> str:
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            config[name.strip()] = value.strip()
    if not config.get("key"):
        raise RuntimeError(f"ECDS credentials are missing from {path}")
    return config["key"]


def get_json(url: str, key: str, *, params: Any = None, timeout: int = 180) -> Any:
    response = requests.get(
        url,
        headers={"PRIVATE-TOKEN": key},
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def recent_successful_jobs(
    key: str,
    *,
    created_after: datetime,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    """List successful ECDS jobs, stopping once a page predates the cutoff."""

    url = JOBS_URL
    params: Any = [
        ("limit", page_size),
        ("sortby", "-created"),
        ("status", "successful"),
    ]
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    while url:
        payload = get_json(url, key, params=params)
        params = None
        page = payload.get("jobs", [])
        for job in page:
            job_id = str(job.get("jobID", ""))
            created = str(job.get("created", ""))
            if job_id and created and parse_utc(created) >= created_after and job_id not in seen:
                output.append(job)
                seen.add(job_id)
        if not page or min(parse_utc(str(job["created"])) for job in page) < created_after:
            break
        url = next(
            (
                urljoin(JOBS_URL, str(link["href"]))
                for link in payload.get("links", [])
                if link.get("rel") == "next" and link.get("href")
            ),
            "",
        )
    return output


def cycle_from_request(request: dict[str, Any]) -> str | None:
    date_value = request.get("date", "")
    time_value = request.get("time", "")
    if isinstance(date_value, list):
        if len(date_value) != 1:
            return None
        date_value = date_value[0]
    if isinstance(time_value, list):
        if len(time_value) != 1:
            return None
        time_value = time_value[0]
    date = str(date_value).replace("-", "")
    hour = str(time_value).split(":", 1)[0]
    if len(date) != 8 or len(hour) not in {1, 2}:
        raise ValueError("ECDS receipt has no usable TIGGE date/time")
    return f"{date}{int(hour):02d}"


def mars_values(value: Any) -> set[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = str(value).split("/")
    return {str(item) for item in parts if str(item)}


def is_full_cycle_request(request: dict[str, Any], model: str) -> bool:
    """Exclude field probes and other partial jobs from whole-cycle recovery."""

    required_parameters = {"131", "132", "151", "165", "166"}
    required_levels = {"500", "700", "850"}
    centre = TIGGE_CENTRES[model]
    try:
        steps = {int(value) for value in mars_values(request.get("step", ""))}
    except ValueError:
        return False
    return (
        required_parameters.issubset(mars_values(request.get("param", "")))
        and {"pl", "sfc"}.issubset(mars_values(request.get("levtype", "")))
        and required_levels.issubset(mars_values(request.get("levelist", "")))
        and set(centre.forecast_types).issubset(mars_values(request.get("type", "")))
        and steps == set(range(0, centre.maximum_horizon_hours + 1, 6))
    )


def inspect_job(
    job: dict[str, Any],
    key: str,
    *,
    wanted_models: set[str],
    public_root: Path,
) -> dict[str, Any] | None:
    job_id = str(job["jobID"])
    base = f"{JOBS_URL}/{job_id}"
    receipt = get_json(f"{base}/receipt", key)
    if receipt.get("collection-id") != "tigge-forecasts":
        return None
    request = receipt.get("request", {})
    model = ORIGIN_MODELS.get(str(request.get("origin", "")))
    if model is None or model not in wanted_models:
        return None
    if not is_full_cycle_request(request, model):
        return None
    cycle = cycle_from_request(request)
    if cycle is None:
        return None
    public_asset = public_root / "tigge" / model / f"{cycle}.json.gz"
    record: dict[str, Any] = {
        "job_id": job_id,
        "model": model,
        "cycle": cycle,
        "created_utc": iso_z(parse_utc(str(receipt["created-at"]))),
        "finished_utc": iso_z(parse_utc(str(receipt["finished-at"]))),
        "published": public_asset.is_file(),
        "request": request,
    }
    if record["published"]:
        return record
    result = get_json(f"{base}/results", key)
    asset = result.get("asset", {}).get("value", {})
    if not asset.get("href"):
        raise RuntimeError(f"ECDS job {job_id} has no downloadable asset")
    record.update(
        {
            "asset_url": str(asset["href"]),
            "checksum": str(asset.get("file:checksum", "")),
            "size": int(asset.get("file:size", 0)),
        }
    )
    return record


def inspect_jobs(
    jobs: Iterable[dict[str, Any]],
    key: str,
    *,
    wanted_models: set[str],
    public_root: Path,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                inspect_job,
                job,
                key,
                wanted_models=wanted_models,
                public_root=public_root,
            ): str(job.get("jobID", ""))
            for job in jobs
        }
        for future in as_completed(futures):
            job_id = futures[future]
            try:
                record = future.result()
            except Exception as error:
                errors.append({"job_id": job_id, "message": str(error)})
            else:
                if record is not None:
                    records.append(record)
    records.sort(key=lambda item: (item["model"], item["cycle"], item["finished_utc"]))
    errors.sort(key=lambda item: item["job_id"])
    return records, errors


def checksum(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_result_valid(path: Path, record: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    expected_size = int(record.get("size", 0))
    expected_checksum = str(record.get("checksum", ""))
    if expected_size and path.stat().st_size != expected_size:
        return False
    return not expected_checksum or checksum(path) == expected_checksum


def download_result(
    record: dict[str, Any], cache_root: Path, *, attempts: int = 3
) -> Path:
    target = cache_root / record["model"] / record["cycle"] / "all.grib"
    if cached_result_valid(target, record):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.recover.part")
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        temporary.unlink(missing_ok=True)
        try:
            with requests.get(
                str(record["asset_url"]), stream=True, timeout=(30, 300)
            ) as response:
                response.raise_for_status()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            if not cached_result_valid(temporary, record):
                expected_size = int(record.get("size", 0))
                actual_size = temporary.stat().st_size
                expected_checksum = str(record.get("checksum", ""))
                actual_checksum = checksum(temporary) if expected_checksum else "not requested"
                raise RuntimeError(
                    "Downloaded ECDS result failed QA "
                    f"(size {actual_size}/{expected_size}; checksum "
                    f"{actual_checksum}/{expected_checksum}): {target}"
                )
            os.replace(temporary, target)
            return target
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(2**attempt)
        finally:
            temporary.unlink(missing_ok=True)
    assert last_error is not None
    raise last_error


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def write_jobs(path: Path, records: Iterable[dict[str, Any]]) -> int:
    rows = sorted({(record["model"], record["cycle"]) for record in records})
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8") as stream:
        for index, (model, cycle) in enumerate(rows, start=1):
            horizon = TIGGE_CENTRES[model].maximum_horizon_hours
            stream.write(f"{index}\t{model}\t{cycle}\t{horizon}\t0\n")
    os.replace(temporary, path)
    return len(rows)


def staged_cycle_complete(run_root: Path, model: str, cycle: str) -> bool:
    manifest_path = run_root / f"{model}-{cycle}" / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return any(
        str(entry.get("model", "")) == model
        and str(entry.get("cycle", "")) == cycle
        for entry in manifest.get("tigge_archive", [])
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--created-after", help="inclusive UTC timestamp")
    parser.add_argument(
        "--from-inventory",
        type=Path,
        help="reuse a previous scan instead of querying every ECDS receipt again",
    )
    parser.add_argument(
        "--resume-inventory",
        action="store_true",
        help="inspect only newly successful job IDs and merge them into the inventory",
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument(
        "--models",
        default="tigge-imd,tigge-ncmrwf",
        help="comma-separated TIGGE model IDs",
    )
    parser.add_argument("--credentials", type=Path, default=Path.home() / ".cdsapirc")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--download-workers", type=int, default=4)
    parser.add_argument("--max-downloads", type=int)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--jobs", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wanted_models = {item.strip() for item in args.models.split(",") if item.strip()}
    unknown = wanted_models - set(TIGGE_CENTRES)
    if unknown:
        raise ValueError("Unknown TIGGE models: " + ", ".join(sorted(unknown)))
    if args.from_inventory:
        previous = json.loads(args.from_inventory.read_text(encoding="utf-8"))
        created_after = parse_utc(str(previous["created_after_utc"]))
        summaries = [None] * int(previous.get("successful_jobs_scanned", 0))
        records = [
            dict(record)
            for record in previous.get("records", [])
            if record.get("model") in wanted_models
        ]
        errors = list(previous.get("errors", []))
        for record in records:
            public_asset = (
                args.public_root
                / "tigge"
                / record["model"]
                / f"{record['cycle']}.json.gz"
            )
            record["published"] = public_asset.is_file()
    else:
        if not args.created_after:
            raise ValueError("--created-after is required unless --from-inventory is used")
        created_after = parse_utc(args.created_after)
        key = credentials(args.credentials)
        summaries = recent_successful_jobs(key, created_after=created_after)
        previous_records: list[dict[str, Any]] = []
        previous_errors: list[dict[str, str]] = []
        inventory_path = args.inventory or args.run_root / "ecds-recovery.json"
        if args.resume_inventory and inventory_path.is_file():
            previous = json.loads(inventory_path.read_text(encoding="utf-8"))
            previous_records = list(previous.get("records", []))
            previous_errors = list(previous.get("errors", []))
        known_job_ids = {str(record.get("job_id", "")) for record in previous_records}
        pending_summaries = [
            job for job in summaries if str(job.get("jobID", "")) not in known_job_ids
        ]
        pending_ids = {str(job.get("jobID", "")) for job in pending_summaries}
        previous_errors = [
            error
            for error in previous_errors
            if str(error.get("job_id", "")) not in pending_ids
        ]
        new_records, new_errors = inspect_jobs(
            pending_summaries,
            key,
            wanted_models=wanted_models,
            public_root=args.public_root,
            workers=args.workers,
        )
        records = previous_records + new_records
        errors = previous_errors + new_errors
        for record in records:
            public_asset = (
                args.public_root
                / "tigge"
                / record["model"]
                / f"{record['cycle']}.json.gz"
            )
            record["published"] = public_asset.is_file()
    # Multiple local attempts can refer to the same model cycle. Keep the most
    # recent successful result; only full-cycle requests pass inspection.
    unique = {
        (record["model"], record["cycle"]): record
        for record in records
    }
    records = sorted(unique.values(), key=lambda item: (item["model"], item["cycle"]))
    for record in records:
        public_asset = (
            args.public_root
            / "tigge"
            / record["model"]
            / f"{record['cycle']}.json.gz"
        )
        record["published"] = public_asset.is_file()
        record["staged"] = staged_cycle_complete(
            args.run_root, record["model"], record["cycle"]
        )
    recoverable = [
        record
        for record in records
        if not record["published"] and not record["staged"]
    ]
    downloaded: list[dict[str, Any]] = []
    if args.download:
        candidates = recoverable[: args.max_downloads]
        with ThreadPoolExecutor(max_workers=max(1, args.download_workers)) as executor:
            futures = {
                executor.submit(download_result, record, args.run_root / "raw-tigge"): record
                for record in candidates
            }
            for future in as_completed(futures):
                record = futures[future]
                try:
                    path = future.result()
                except Exception as error:
                    errors.append({"job_id": record["job_id"], "message": str(error)})
                else:
                    record["cache_path"] = str(path)
                    downloaded.append(record)
                    print(f"Recovered {record['model']} {record['cycle']} to {path}")
    inventory_path = args.inventory or args.run_root / "ecds-recovery.json"
    jobs_path = args.jobs or args.run_root / "recovered-jobs.tsv"
    cache_root = args.run_root / "raw-tigge"
    ready = [
        record
        for record in recoverable
        if cached_result_valid(
            cache_root / record["model"] / record["cycle"] / "all.grib",
            record,
        )
    ]
    payload = {
        "schema": "mla-ecds-tigge-recovery-v1",
        "generated_utc": iso_z(datetime.now(UTC)),
        "created_after_utc": iso_z(created_after),
        "models": sorted(wanted_models),
        "successful_jobs_scanned": len(summaries),
        "matching_model_cycles": len(records),
        "already_published": sum(bool(record["published"]) for record in records),
        "already_staged": sum(bool(record["staged"]) for record in records),
        "recoverable": len(recoverable),
        "downloaded": len(ready),
        "records": records,
        "errors": sorted(errors, key=lambda item: item["job_id"]),
    }
    atomic_write_json(inventory_path, payload)
    count = write_jobs(jobs_path, ready)
    print(
        f"Scanned {len(summaries)} successful ECDS jobs: {len(records)} selected cycles, "
        f"{len(recoverable)} unpublished, {count} ready for local processing"
    )


if __name__ == "__main__":
    main()
