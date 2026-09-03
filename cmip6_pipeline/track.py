#!/usr/bin/env python3
"""Run standardized CMIP6 fields through the frozen v5.6 detector/linker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from reanalysis_pipeline.common import sha256
from cmip6_pipeline.standardise import validate_month


ATLAS_ROOT = Path(__file__).resolve().parents[1]
TRACKER_ROOT = ATLAS_ROOT.parent / "lps-v5.3-continuity-framework"
DETECTOR = TRACKER_ROOT / "lps53_detect.py"
LINKER = TRACKER_ROOT / "lps53_link.py"
PARAMETERS = TRACKER_ROOT / "params/lps_v5.4.2_liberal_poststitch_identity.json"


def run(command: list[str]) -> None:
    print(" ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=TRACKER_ROOT)


def detect_month(data_root: Path, output_root: Path, month: str) -> Path:
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    validate_month(data_root, month)
    standard = data_root / "standard"
    destination = output_root / "candidates"
    run(
        [
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
    )
    path = destination / f"candidates-{month}.csv"
    if not path.is_file() or path.stat().st_size < 100:
        raise RuntimeError(f"detector did not produce {path}")
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
    linker = subparsers.add_parser("link")
    linker.add_argument("--output-root", type=Path, required=True)
    linker.add_argument("--source-label", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "detect-month":
        path = detect_month(args.data_root, args.output_root, args.month)
    else:
        path = link(args.output_root, args.source_label)
    print(path)


if __name__ == "__main__":
    main()
