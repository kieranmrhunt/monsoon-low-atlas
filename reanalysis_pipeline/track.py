#!/usr/bin/env python3
"""Run standardized alternative reanalysis fields through the frozen linker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .common import sha256
from .standardise_merra2 import validate_month


ATLAS_ROOT = Path(__file__).resolve().parents[1]
TRACKER_ROOT = ATLAS_ROOT.parent / "lps-v5.3-continuity-framework"
DETECTOR = TRACKER_ROOT / "lps53_detect.py"
LINKER = TRACKER_ROOT / "lps53_link.py"
PARAMETERS = TRACKER_ROOT / "params/lps_v5.4.2_liberal_poststitch_identity.json"
TRACKING_SCHEMA = "lps-atlas-reanalysis-tracking-v1"


def run(command: list[str]) -> None:
    print(" ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=TRACKER_ROOT)


def source_paths(data_root: Path) -> dict[str, Path]:
    standard = data_root.resolve() / "standard"
    return {
        "vorticity": standard / "vorticity",
        "surface": standard / "surface",
        "precipitation": standard / "precipitation",
        "auxiliary": standard / "auxiliary",
    }


def detect_month(data_root: Path, output_root: Path, month: str, source: str) -> Path:
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    if source == "merra2":
        validate_month(data_root, month)
    paths = source_paths(data_root)
    candidates = output_root / "candidates"
    log = output_root / "logs" / f"detect-{month}.log"
    run([
        sys.executable,
        str(DETECTOR),
        month,
        "--params", str(PARAMETERS),
        "--vort-dir", str(paths["vorticity"]),
        "--precip-dir", str(paths["precipitation"]),
        "--sfc-dir", str(paths["surface"]),
        "--aux-dir", str(paths["auxiliary"]),
        "--out-dir", str(candidates),
        "--log-file", str(log),
        "--progress-every", "24",
    ])
    path = candidates / f"candidates-{month}.csv"
    if not path.is_file() or path.stat().st_size < 100:
        raise RuntimeError(f"detector did not produce a valid candidate file: {path}")
    return path


def link_candidates(output_root: Path, source: str) -> Path:
    output_root = output_root.resolve()
    candidates = sorted((output_root / "candidates").glob("candidates-*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No candidate files below {output_root / 'candidates'}")
    candidate_list = output_root / "candidate-files.txt"
    candidate_list.parent.mkdir(parents=True, exist_ok=True)
    temporary = candidate_list.with_suffix(f".txt.part-{os.getpid()}")
    temporary.write_text("".join(f"{path.resolve()}\n" for path in candidates), encoding="utf-8")
    os.replace(temporary, candidate_list)
    linked = output_root / f"{source}-linked.csv"
    run([
        sys.executable,
        str(LINKER),
        "--candidate-list", str(candidate_list),
        "--params", str(PARAMETERS),
        "--out", str(linked),
        "--rejected-out", str(output_root / f"{source}-rejected.csv"),
        "--links-out", str(output_root / f"{source}-stitches.csv"),
        "--summary-out", str(output_root / f"{source}-link-summary.json"),
    ])
    if not linked.is_file():
        raise RuntimeError(f"linker did not produce {linked}")
    manifest = {
        "schema": TRACKING_SCHEMA,
        "source": source,
        "candidate_months": [path.stem.removeprefix("candidates-") for path in candidates],
        "candidate_files": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in candidates
        ],
        "linked": {"path": str(linked), "bytes": linked.stat().st_size, "sha256": sha256(linked)},
        "detector": str(DETECTOR),
        "linker": str(LINKER),
        "parameters": {"path": str(PARAMETERS), "sha256": sha256(PARAMETERS)},
        "method_note": "Frozen v5.6 detector/linker geometry; ERA5 event identity and release category are assigned only after objective track matching.",
    }
    manifest_path = output_root / f"{source}-tracking-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return linked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("merra2", "imdaa"), required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    detect = subparsers.add_parser("detect-month")
    detect.add_argument("--month", required=True, help="YYYYMM")
    subparsers.add_parser("link")
    run_month = subparsers.add_parser("run-month")
    run_month.add_argument("--month", required=True, help="YYYYMM")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command in ("detect-month", "run-month"):
        detect_month(args.data_root, args.output_root, args.month, args.source)
    if args.command in ("link", "run-month"):
        path = link_candidates(args.output_root, args.source)
        print(path)


if __name__ == "__main__":
    main()
