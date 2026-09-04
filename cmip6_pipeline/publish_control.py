#!/usr/bin/env python3
"""Attach the validated ERA5 common-grid control to a climate browser bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from reanalysis_pipeline.common import sha256

from .era5_control import SOURCE_LABEL, utc_now
from .summarise import INDEX_SCHEMA, SCHEMA, atomic_gzip_json, atomic_json


CONTROL_ID = "era5-common-1deg-1981-2010"


def _load_gzip(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".part-{os.getpid()}")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def attach_resolution_control(climate_manifest: Path, summary_manifest: Path) -> Path:
    climate_manifest = climate_manifest.resolve()
    summary_manifest = summary_manifest.resolve()
    output_root = climate_manifest.parent

    manifest = json.loads(climate_manifest.read_text(encoding="utf-8"))
    if manifest.get("schema") != INDEX_SCHEMA:
        raise ValueError("climate manifest has an unsupported schema")
    index_path = output_root / manifest["index"]["path"]
    if sha256(index_path) != manifest["index"]["sha256"]:
        raise ValueError("climate index checksum does not match its manifest")
    index = _load_gzip(index_path)
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError("climate index has an unsupported schema")

    summary_meta = json.loads(summary_manifest.read_text(encoding="utf-8"))
    if summary_meta.get("schema") != SCHEMA:
        raise ValueError("control summary manifest has an unsupported schema")
    source = Path(summary_meta["asset"]["path"])
    if not source.is_absolute():
        source = summary_manifest.parent / source
    if sha256(source) != summary_meta["asset"]["sha256"]:
        raise ValueError("control summary checksum does not match its manifest")
    payload = _load_gzip(source)
    if payload.get("schema") != SCHEMA:
        raise ValueError("control summary asset has an unsupported schema")
    if payload.get("run", {}).get("source_label") != SOURCE_LABEL:
        raise ValueError(f"control source must be {SOURCE_LABEL}")
    coverage = payload.get("coverage") or {}
    if (coverage.get("start_year"), coverage.get("end_year"), coverage.get("years")) != (1981, 2010, 30):
        raise ValueError("control must cover the complete 1981--2010 period")
    qa = payload.get("qa") or {}
    if qa.get("status") != "passed" or not qa.get("historical_screen"):
        raise ValueError("control must pass QA and include its native-ERA5 comparison")

    destination = output_root / "assets" / f"climate-control-era5-common-1deg.{sha256(source)[:12]}.json.gz"
    _copy_atomic(source, destination)
    index["resolution_controls"] = [
        {
            "id": CONTROL_ID,
            "label": "ERA5 · common 1°",
            "run": payload["run"],
            "coverage": coverage,
            "summary": {
                "url": f"assets/{destination.name}",
                "sha256": sha256(destination),
                "bytes": destination.stat().st_size,
            },
        }
    ]
    index["generated_utc"] = utc_now()
    raw = json.dumps(index, separators=(",", ":"), allow_nan=False).encode("utf-8")
    new_index = output_root / f"climate-index.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
    atomic_gzip_json(new_index, index)
    manifest.update(
        {
            "generated_utc": index["generated_utc"],
            "index": {
                "path": new_index.name,
                "sha256": sha256(new_index),
                "bytes": new_index.stat().st_size,
            },
            "resolution_controls": 1,
        }
    )
    atomic_json(climate_manifest, manifest)
    return new_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--climate-manifest", type=Path, required=True)
    parser.add_argument("--summary-manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(attach_resolution_control(args.climate_manifest, args.summary_manifest))


if __name__ == "__main__":
    main()
