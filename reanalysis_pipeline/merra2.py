#!/usr/bin/env python3
"""Download compact MERRA-2 fields for the LPS reanalysis comparison.

NASA's Cloud OPeNDAP endpoint performs the spatial, temporal and variable
subsetting.  Earthdata credentials are read from ``~/.netrc`` and existing
cookies from ``~/.urs_cookies``; neither is copied into outputs or logs.
"""

from __future__ import annotations

import argparse
import calendar
import json
import netrc
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

import xarray as xr

from .common import require_variables, sha256


EARTHDATA_MACHINE = "urs.earthdata.nasa.gov"
COOKIE_PATH = Path.home() / ".urs_cookies"
COLLECTIONS: Mapping[str, dict[str, Any]] = {
    "pressure": {
        "concept_id": "C1276812879-GES_DISC",
        "short_name": "M2I3NPASM",
        "granule_product": "inst3_3d_asm_Np",
        "variables": ("U", "V", "T", "RH"),
    },
    "surface": {
        "concept_id": "C1276812820-GES_DISC",
        "short_name": "M2I1NXASM",
        "granule_product": "inst1_2d_asm_Nx",
        "variables": ("U10M", "V10M", "SLP", "PS"),
    },
}
VERSION = "5.12.4"
LATITUDE_SLICE = "150:1:270"  # -15 to 45 degrees north at 0.5 degrees
LONGITUDE_SLICE = "360:1:480"  # 45 to 120 degrees east at 0.625 degrees
PRESSURE_SLICE = "6:2:16"  # 850, 800, 750, 700, 600 and 500 hPa
LEDGER_SCHEMA = "lps-atlas-merra2-opendap-v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def stream_number(day: date) -> int:
    if day.year <= 1991:
        return 100
    if day.year <= 2000:
        return 200
    if day.year <= 2010:
        return 300
    return 400


def granule_name(kind: str, day: date) -> str:
    collection = COLLECTIONS[kind]
    return (
        f"{collection['short_name']}.{VERSION}:"
        f"MERRA2_{stream_number(day)}.{collection['granule_product']}.{day:%Y%m%d}.nc4"
    )


def opendap_url(kind: str, day: date) -> str:
    collection = COLLECTIONS[kind]
    granule = quote(granule_name(kind, day), safe="")
    return (
        "https://opendap.earthdata.nasa.gov/collections/"
        f"{collection['concept_id']}/granules/{granule}.dap.nc4"
    )


def constraint(kind: str) -> str:
    if kind == "pressure":
        coordinates = (
            "/time[0:1:7]",
            f"/lev[{PRESSURE_SLICE}]",
            f"/lat[{LATITUDE_SLICE}]",
            f"/lon[{LONGITUDE_SLICE}]",
        )
        variables = tuple(
            f"/{name}[0:1:7][{PRESSURE_SLICE}][{LATITUDE_SLICE}][{LONGITUDE_SLICE}]"
            for name in COLLECTIONS[kind]["variables"]
        )
    elif kind == "surface":
        coordinates = (
            "/time[0:3:21]",
            f"/lat[{LATITUDE_SLICE}]",
            f"/lon[{LONGITUDE_SLICE}]",
        )
        variables = tuple(
            f"/{name}[0:3:21][{LATITUDE_SLICE}][{LONGITUDE_SLICE}]"
            for name in COLLECTIONS[kind]["variables"]
        )
    else:
        raise ValueError(f"unknown MERRA-2 request kind {kind!r}")
    return ";".join((*coordinates, *variables))


def new_ledger() -> dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "source": "NASA GES DISC MERRA-2 Cloud OPeNDAP",
        "created_utc": utc_now(),
        "updated_utc": utc_now(),
        "requests": {},
    }


def read_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_ledger()
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != LEDGER_SCHEMA or not isinstance(value.get("requests"), dict):
        raise ValueError(f"unsupported or malformed MERRA-2 ledger: {path}")
    return value


def write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_utc"] = utc_now()
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass
class EarthdataSession:
    """Credential-checked transfer client using curl's Earthdata support."""

    credentials_available: bool

    @classmethod
    def login(cls) -> "EarthdataSession":
        credentials = netrc.netrc().authenticators(EARTHDATA_MACHINE)
        if credentials is None:
            raise RuntimeError(f"No {EARTHDATA_MACHINE} entry is present in ~/.netrc")
        return cls(True)

    def download(self, kind: str, day: date, destination: Path, *, attempts: int = 6) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
        encoded_constraint = quote(constraint(kind), safe="/:;")
        url = f"{opendap_url(kind, day)}?dap4.ce={encoded_constraint}"
        last_error: Exception | None = None
        for attempt in range(attempts):
            cookie = Path(tempfile.gettempdir()) / f"merra2-cookie-{os.getpid()}-{attempt}.txt"
            try:
                command = [
                    "curl",
                    "--globoff",
                    "-fLsS",
                    "--netrc",
                    "--connect-timeout",
                    "60",
                    "--max-time",
                    "900",
                    "--retry",
                    "4",
                    "--retry-delay",
                    "2",
                    "--retry-all-errors",
                ]
                if COOKIE_PATH.exists():
                    command.extend(["-b", str(COOKIE_PATH)])
                command.extend(["-c", str(cookie), "-o", str(temporary), url])
                subprocess.run(command, check=True, capture_output=True, text=True)
                validate_download(kind, day, temporary)
                os.replace(temporary, destination)
                return destination
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                last_error = error
                temporary.unlink(missing_ok=True)
                if attempt + 1 < attempts:
                    time.sleep(min(60.0, 2.0 ** attempt))
            finally:
                cookie.unlink(missing_ok=True)
        raise RuntimeError(f"MERRA-2 {kind} download failed for {day}: {last_error}")


def validate_download(kind: str, day: date, path: Path) -> None:
    if not path.exists() or path.stat().st_size < 10_000:
        raise ValueError(f"MERRA-2 response is unexpectedly small: {path}")
    with xr.open_dataset(path) as dataset:
        require_variables(dataset, COLLECTIONS[kind]["variables"], path)
        expected_times = 8
        if dataset.sizes.get("time") != expected_times:
            raise ValueError(f"{path} has {dataset.sizes.get('time')} times, expected {expected_times}")
        if dataset.sizes.get("lat") != 121 or dataset.sizes.get("lon") != 121:
            raise ValueError(f"{path} does not cover the requested regional grid")
        if kind == "pressure":
            levels = [round(float(value)) for value in dataset["lev"].values]
            if not {850, 700, 500}.issubset(levels):
                raise ValueError(f"{path} lacks one of 850, 700 or 500 hPa")
        first = datetime.fromisoformat(str(dataset.time.values[0])[:19])
        if first.date() != day:
            raise ValueError(f"{path} begins on {first.date()}, expected {day}")


def days_in_month(value: str, *, include_next_midnight: bool = True) -> list[date]:
    parsed = datetime.strptime(value, "%Y-%m").date().replace(day=1)
    count = calendar.monthrange(parsed.year, parsed.month)[1]
    output = [parsed + timedelta(days=index) for index in range(count)]
    if include_next_midnight:
        output.append(parsed + timedelta(days=count))
    return output


def output_path(root: Path, kind: str, day: date) -> Path:
    return root / "raw" / kind / f"{day:%Y}" / f"merra2-{kind}-{day:%Y%m%d}.nc4"


def download_days(
    root: Path,
    ledger_path: Path,
    days: Iterable[date],
    *,
    kinds: Iterable[str] = ("pressure", "surface"),
) -> dict[str, int]:
    """Download days without concurrent writes to the shared ledger.

    The Slurm array can span many hosts, while the GWS does not provide a
    sufficiently reliable cross-node advisory lock for a single JSON file.
    Each downloaded NetCDF file is already atomically installed and validated.
    ``reconcile_days`` therefore creates the authoritative ledger serially
    after every array task has completed.
    """

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    client = EarthdataSession.login()
    counts = {"downloaded": 0, "reused": 0}
    for day in days:
        for kind in kinds:
            destination = output_path(root, kind, day)
            key = f"{kind}-{day:%Y%m%d}"
            if destination.exists():
                try:
                    validate_download(kind, day, destination)
                    counts["reused"] += 1
                    continue
                except ValueError:
                    destination.unlink()
            client.download(kind, day, destination)
            counts["downloaded"] += 1
            print(f"downloaded {key} ({destination.stat().st_size:,} bytes)", flush=True)
    return counts


def reconcile_days(
    root: Path,
    ledger_path: Path,
    days: Iterable[date],
    *,
    kinds: Iterable[str] = ("pressure", "surface"),
) -> dict[str, int]:
    """Rebuild ledger records from validated files after a parallel array.

    Some shared filesystems do not provide reliable cross-node advisory locks.
    The files themselves are atomically written and independently validated;
    this serial pass makes their provenance ledger authoritative afterward.
    """

    ledger = read_ledger(ledger_path)
    reconciled = 0
    for day in days:
        for kind in kinds:
            destination = output_path(root, kind, day)
            validate_download(kind, day, destination)
            key = f"{kind}-{day:%Y%m%d}"
            ledger["requests"][key] = {
                "kind": kind,
                "date": day.isoformat(),
                "concept_id": COLLECTIONS[kind]["concept_id"],
                "granule": granule_name(kind, day),
                "constraint": constraint(kind),
                "local_path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": sha256(destination),
                "completed_utc": utc_now(),
                "ledger_basis": "post-array validated-file reconciliation",
            }
            reconciled += 1
    write_ledger(ledger_path, ledger)
    return {"reconciled": reconciled}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/reanalyses/merra2"))
    parser.add_argument("--ledger", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    day = subparsers.add_parser("download-day")
    day.add_argument("--date", required=True)
    month = subparsers.add_parser("download-month")
    month.add_argument("--month", required=True)
    reconcile = subparsers.add_parser("reconcile-month")
    reconcile.add_argument("--month", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ledger = args.ledger or args.root / "opendap-ledger.json"
    if args.command == "download-day":
        selected = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    elif args.command == "download-month":
        selected = days_in_month(args.month)
    else:
        selected = days_in_month(args.month)
        print(json.dumps(reconcile_days(args.root, ledger, selected), sort_keys=True))
        return
    print(json.dumps(download_days(args.root, ledger, selected), sort_keys=True))


if __name__ == "__main__":
    main()
