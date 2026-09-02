#!/usr/bin/env python3
"""Submit and retrieve reproducible IMDAA requests from NCMRWF RDS.

Credentials are read from ``~/.netrc`` for ``rds.ncmrwf.gov.in``.  They are
never written to the request ledger or printed.  The ledger is deliberately
small: it records the scientific request, remote job identifier, status and
local checksum so interrupted backfills can be resumed without duplication.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import netrc
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlparse

import requests


BASE_URL = "https://rds.ncmrwf.gov.in/api"
NETRC_MACHINE = "rds.ncmrwf.gov.in"
DEFAULT_AREA = {"north": 45, "south": -15, "east": 120, "west": 45}
PRESSURE_LEVELS = (850, 700, 500)
PRESSURE_VARIABLES = ("UGRD-prl", "VGRD-prl", "TMP-prl", "RH-prl")
SURFACE_VARIABLES = ("UGRD-10m", "VGRD-10m", "PRMSL-msl", "APCP-sfc")
PRESSURE_TIMES = tuple(f"{hour:02d}" for hour in range(0, 24, 3))
SURFACE_TIMES = PRESSURE_TIMES
LEDGER_SCHEMA = "lps-atlas-imdaa-rds-v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_days(year: int, month: int) -> tuple[str, ...]:
    return tuple(f"{day:02d}" for day in range(1, calendar.monthrange(year, month)[1] + 1))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_key(kind: str, year: int, month: int, days: Sequence[str]) -> str:
    day_part = "all" if tuple(days) == month_days(year, month) else "-".join(days)
    return f"imdaa-{kind}-{year:04d}{month:02d}-{day_part}"


def request_payload(
    kind: str,
    year: int,
    month: int,
    *,
    days: Sequence[str] | None = None,
    area: Mapping[str, int] = DEFAULT_AREA,
) -> dict[str, Any]:
    """Return the normalized payload accepted by the current RDS API."""

    selected_days = tuple(days or month_days(year, month))
    invalid_days = sorted(set(selected_days) - set(month_days(year, month)))
    if invalid_days:
        raise ValueError(f"Invalid days for {year:04d}-{month:02d}: {invalid_days}")
    common: dict[str, Any] = {
        "year": str(year),
        "month": [f"{month:02d}"],
        "day": list(selected_days),
        "data_format": ["netcdf4_experimental"],
        "download_format": ["zip"],
        "area": {name: int(area[name]) for name in ("north", "south", "east", "west")},
    }
    if kind == "pressure":
        return {
            **common,
            "dataset_type": "prl",
            "time": list(PRESSURE_TIMES),
            "pressure_level": [f"{level}_hpa" for level in PRESSURE_LEVELS],
            "variables": list(PRESSURE_VARIABLES),
        }
    if kind == "surface":
        return {
            **common,
            "dataset_type": "2df",
            "frequency": "1h",
            "time": list(SURFACE_TIMES),
            "variables": list(SURFACE_VARIABLES),
        }
    raise ValueError(f"Unknown IMDAA request kind {kind!r}")


def new_ledger() -> dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "source": "NCMRWF Reanalysis Data Service",
        "base_url": BASE_URL,
        "created_utc": utc_now(),
        "updated_utc": utc_now(),
        "requests": {},
    }


def read_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_ledger()
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != LEDGER_SCHEMA:
        raise ValueError(f"Unsupported ledger schema in {path}")
    if not isinstance(value.get("requests"), dict):
        raise ValueError(f"Malformed request ledger {path}")
    return value


def write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_utc"] = utc_now()
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass
class RDSClient:
    session: requests.Session

    @classmethod
    def login(cls, *, timeout: float = 60.0) -> "RDSClient":
        credentials = netrc.netrc().authenticators(NETRC_MACHINE)
        if credentials is None:
            raise RuntimeError(f"No {NETRC_MACHINE} entry is present in ~/.netrc")
        username, unused_account, password = credentials
        session = requests.Session()
        response = session.post(
            f"{BASE_URL}/auth/login",
            json={"email": username, "password": password},
            timeout=timeout,
        )
        response.raise_for_status()
        return cls(session)

    def submit(self, payload: Mapping[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        response = self.session.post(
            f"{BASE_URL}/jobs",
            json={"request_payload": dict(payload)},
            timeout=timeout,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or value.get("id") is None:
            raise ValueError("NCMRWF returned a job response without an id")
        return value

    def jobs(self, *, timeout: float = 60.0) -> list[dict[str, Any]]:
        response = self.session.get(f"{BASE_URL}/jobs/my", timeout=timeout)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, list):
            raise ValueError("NCMRWF /jobs/my returned a non-list response")
        return [row for row in value if isinstance(row, dict)]

    def download_url(self, job_id: int | str, *, timeout: float = 60.0) -> str:
        response = self.session.get(f"{BASE_URL}/jobs/download/{job_id}/type", timeout=timeout)
        response.raise_for_status()
        value = response.json()
        url = value.get("download_url") if isinstance(value, dict) else None
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError(f"NCMRWF job {job_id} has no usable download URL")
        return url

    def download(self, job_id: int | str, destination: Path, *, timeout: float = 1800.0) -> Path:
        url = self.download_url(job_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
        with self.session.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with temporary.open("wb") as stream:
                for chunk in response.iter_content(4 * 1024 * 1024):
                    if chunk:
                        stream.write(chunk)
        if temporary.stat().st_size == 0:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"NCMRWF job {job_id} produced an empty download")
        os.replace(temporary, destination)
        if destination.suffix.lower() == ".zip" and not zipfile.is_zipfile(destination):
            raise ValueError(f"NCMRWF job {job_id} did not produce a valid ZIP archive")
        return destination


def plan_requests(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> Iterable[tuple[str, int, int, tuple[str, ...], dict[str, Any]]]:
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        days = month_days(year, month)
        for kind in ("pressure", "surface"):
            yield request_key(kind, year, month, days), year, month, days, request_payload(
                kind, year, month, days=days
            )
        month += 1
        if month == 13:
            month = 1
            year += 1


def safe_status(row: Mapping[str, Any]) -> str:
    return str(row.get("status", "unknown")).strip().lower().replace(" ", "_")


def submit_requests(
    ledger_path: Path,
    requests_to_submit: Iterable[tuple[str, int, int, Sequence[str], Mapping[str, Any]]],
    *,
    maximum: int | None = None,
) -> int:
    ledger = read_ledger(ledger_path)
    client = RDSClient.login()
    submitted = 0
    for key, year, month, days, payload in requests_to_submit:
        if key in ledger["requests"]:
            continue
        remote = client.submit(payload)
        ledger["requests"][key] = {
            "key": key,
            "year": year,
            "month": month,
            "days": list(days),
            "kind": "pressure" if payload["dataset_type"] == "prl" else "surface",
            "payload": dict(payload),
            "job_id": remote["id"],
            "status": safe_status(remote),
            "submitted_utc": utc_now(),
        }
        write_ledger(ledger_path, ledger)
        submitted += 1
        print(f"submitted {key} as job {remote['id']}", flush=True)
        if maximum is not None and submitted >= maximum:
            break
        time.sleep(0.15)
    return submitted


def refresh_status(ledger_path: Path) -> dict[str, int]:
    ledger = read_ledger(ledger_path)
    client = RDSClient.login()
    remote = {str(row.get("id")): row for row in client.jobs()}
    counts: dict[str, int] = {}
    for record in ledger["requests"].values():
        row = remote.get(str(record.get("job_id")))
        if row is not None:
            record["status"] = safe_status(row)
            for field in ("created_at", "started_at", "completed_at", "error_message"):
                if row.get(field) is not None:
                    record[field] = row[field]
        status = str(record.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    write_ledger(ledger_path, ledger)
    return counts


def download_completed(ledger_path: Path, output_root: Path, *, maximum: int | None = None) -> int:
    ledger = read_ledger(ledger_path)
    client = RDSClient.login()
    completed_statuses = {"completed", "complete", "ready", "success", "succeeded"}
    downloaded = 0
    for key in sorted(ledger["requests"]):
        record = ledger["requests"][key]
        if str(record.get("status")) not in completed_statuses or record.get("sha256"):
            continue
        destination = output_root / f"{int(record['year']):04d}" / f"{int(record['month']):02d}" / f"{key}.zip"
        client.download(record["job_id"], destination)
        record["local_path"] = str(destination)
        record["bytes"] = destination.stat().st_size
        record["sha256"] = sha256(destination)
        record["downloaded_utc"] = utc_now()
        write_ledger(ledger_path, ledger)
        print(f"downloaded {key} ({record['bytes']:,} bytes)", flush=True)
        downloaded += 1
        if maximum is not None and downloaded >= maximum:
            break
    return downloaded


def parse_month(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not match:
        raise argparse.ArgumentTypeError("month must be YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError("month must be YYYY-MM")
    return year, month


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    value.add_argument("--ledger", type=Path, required=True)
    commands = value.add_subparsers(dest="command", required=True)
    canary = commands.add_parser("submit-canary", help="submit both products for one day")
    canary.add_argument("--date", required=True, help="YYYY-MM-DD")
    submit = commands.add_parser("submit-range", help="submit both monthly products over a range")
    submit.add_argument("--start", required=True, type=parse_month)
    submit.add_argument("--end", required=True, type=parse_month)
    submit.add_argument("--maximum", type=int)
    commands.add_parser("status", help="refresh statuses from RDS")
    download = commands.add_parser("download", help="download completed jobs")
    download.add_argument("--output-root", type=Path, required=True)
    download.add_argument("--maximum", type=int)
    return value


def main() -> None:
    args = parser().parse_args()
    if args.command == "submit-canary":
        stamp = datetime.strptime(args.date, "%Y-%m-%d")
        day = (f"{stamp.day:02d}",)
        requests_to_submit = []
        for kind in ("pressure", "surface"):
            key = request_key(kind, stamp.year, stamp.month, day)
            requests_to_submit.append(
                (key, stamp.year, stamp.month, day, request_payload(kind, stamp.year, stamp.month, days=day))
            )
        submit_requests(args.ledger, requests_to_submit)
    elif args.command == "submit-range":
        submit_requests(
            args.ledger,
            plan_requests(*args.start, *args.end),
            maximum=args.maximum,
        )
    elif args.command == "status":
        print(json.dumps(refresh_status(args.ledger), sort_keys=True))
    elif args.command == "download":
        download_completed(args.ledger, args.output_root, maximum=args.maximum)


if __name__ == "__main__":
    main()

