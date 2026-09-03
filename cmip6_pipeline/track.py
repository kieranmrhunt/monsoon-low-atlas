#!/usr/bin/env python3
"""Run standardized CMIP6 fields through the frozen v5.6 detector/linker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from reanalysis_pipeline.common import sha256
from cmip6_pipeline.standardise import validate_month


ATLAS_ROOT = Path(__file__).resolve().parents[1]
TRACKER_ROOT = ATLAS_ROOT.parent / "lps-v5.3-continuity-framework"
DETECTOR = TRACKER_ROOT / "lps53_detect.py"
LINKER = TRACKER_ROOT / "lps53_link.py"
PARAMETERS = TRACKER_ROOT / "params/lps_v5.4.2_liberal_poststitch_identity.json"
TRACKING_SCHEMA = "lps-atlas-cmip6-detection-v2"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(command: list[str]) -> None:
    print(" ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=TRACKER_ROOT)


def _candidate_is_complete(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 100:
        return False
    columns = set(pd.read_csv(path, nrows=0).columns)
    return {"candidate_uid", "time", "lon", "lat", "centre_score"}.issubset(columns)


def detect_month(
    data_root: Path,
    output_root: Path,
    month: str,
    *,
    static_file: Path | None = None,
) -> Path:
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    validate_month(data_root, month)
    standard = data_root / "standard"
    destination = output_root / "candidates"
    path = destination / f"candidates-{month}.csv"
    status_path = output_root / "status" / f"detect-{month}.json"
    inputs = {
        name: sha256(value)
        for name, value in {
            "vorticity": standard / "vorticity" / f"{month}.nc",
            "surface": standard / "surface" / f"{month}.nc",
            "precipitation": standard / "precipitation" / f"{month}.nc",
            "auxiliary": standard / "auxiliary" / f"pl3h-{month}.nc",
        }.items()
    }
    fingerprint = {
        "schema": TRACKING_SCHEMA,
        "month": month,
        "inputs": inputs,
        "detector_sha256": sha256(DETECTOR),
        "parameters_sha256": sha256(PARAMETERS),
        "static_sha256": sha256(static_file.resolve()) if static_file is not None else None,
    }
    if status_path.is_file() and _candidate_is_complete(path):
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if (
                status.get("status") == "complete"
                and status.get("fingerprint") == fingerprint
                and status.get("candidate_sha256") == sha256(path)
            ):
                return path
        except (OSError, KeyError, json.JSONDecodeError):
            pass
    if static_file is not None and not static_file.is_file():
        raise FileNotFoundError(static_file)
    destination.mkdir(parents=True, exist_ok=True)
    (output_root / "logs").mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(DETECTOR),
        month,
        "--params",
        str(PARAMETERS),
        "--vort-dir",
        str(standard / "vorticity"),
        "--precip-dir",
        str(standard / "precipitation"),
        "--sfc-dir",
        str(standard / "surface"),
        "--aux-dir",
        str(standard / "auxiliary"),
        "--out-dir",
        str(destination),
        "--log-file",
        str(output_root / "logs" / f"detect-{month}.log"),
        "--progress-every",
        "24",
    ]
    if static_file is not None:
        command.extend(["--static-file", str(static_file.resolve())])
    temporary = status_path.with_suffix(f".json.part-{os.getpid()}")
    temporary.write_text(
        json.dumps({"status": "running", "started_utc": utc_now(), "fingerprint": fingerprint}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, status_path)
    run(command)
    if not _candidate_is_complete(path):
        raise RuntimeError(f"detector did not produce {path}")
    temporary.write_text(
        json.dumps(
            {
                "status": "complete",
                "completed_utc": utc_now(),
                "fingerprint": fingerprint,
                "candidate": str(path),
                "candidate_sha256": sha256(path),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, status_path)
    return path


def link(output_root: Path, source_label: str) -> Path:
    output_root = output_root.resolve()
    candidates = sorted((output_root / "candidates").glob("candidates-*.csv"))
    if not candidates:
        raise FileNotFoundError(f"no candidates below {output_root}")
    candidate_list = output_root / "candidate-files.txt"
    temporary = candidate_list.with_suffix(f".txt.part-{os.getpid()}")
    temporary.write_text("".join(f"{path.resolve()}\n" for path in candidates), encoding="utf-8")
    os.replace(temporary, candidate_list)
    linked = output_root / f"{source_label}-linked.csv"
    run(
        [
            sys.executable,
            str(LINKER),
            "--candidate-list",
            str(candidate_list),
            "--params",
            str(PARAMETERS),
            "--out",
            str(linked),
            "--rejected-out",
            str(output_root / f"{source_label}-rejected.csv"),
            "--links-out",
            str(output_root / f"{source_label}-stitches.csv"),
            "--summary-out",
            str(output_root / f"{source_label}-link-summary.json"),
        ]
    )
    manifest = {
        "schema": "lps-atlas-cmip6-tracking-v1",
        "source_label": source_label,
        "months": [path.stem.removeprefix("candidates-") for path in candidates],
        "parameters": {"path": str(PARAMETERS), "sha256": sha256(PARAMETERS)},
        "linked": {"path": str(linked), "bytes": linked.stat().st_size, "sha256": sha256(linked)},
        "method_note": "Frozen v5.6 candidate detector and continuity linker; CMIP6 fields standardized to the shared 1-degree input contract without threshold retuning.",
    }
    path = output_root / f"{source_label}-tracking-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return linked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    detect = subparsers.add_parser("detect-month")
    detect.add_argument("--data-root", type=Path, required=True)
    detect.add_argument("--output-root", type=Path, required=True)
    detect.add_argument("--month", required=True)
    detect.add_argument("--static-file", type=Path)
    linker = subparsers.add_parser("link")
    linker.add_argument("--output-root", type=Path, required=True)
    linker.add_argument("--source-label", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "detect-month":
        path = detect_month(
            args.data_root,
            args.output_root,
            args.month,
            static_file=args.static_file,
        )
    else:
        path = link(args.output_root, args.source_label)
    print(path)


if __name__ == "__main__":
    main()
