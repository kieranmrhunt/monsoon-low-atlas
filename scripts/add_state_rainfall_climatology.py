#!/usr/bin/env python3
"""Add an all-record JJAS state-rainfall climatology to an atlas detail asset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_v542_assets import (
    dump_hashed,
    read_dashboard_data,
    read_gzip_json,
    state_jjas_climatology,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--rainfall-data", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    detail = read_gzip_json(args.detail)
    core = read_gzip_json(args.core)
    rainfall = read_dashboard_data(args.rainfall_data)
    means_x10, coverage = state_jjas_climatology(rainfall, core["state_slugs"])
    if len(means_x10) != len(core["states"]):
        raise ValueError("State climatology does not align with the atlas state list")
    detail["state_rainfall"].update({
        "jjas_climatology_x10": means_x10,
        "jjas_climatology_period": coverage,
        "jjas_climatology_months": [6, 7, 8, 9],
        "fractional_anomaly": "event-period state mean / all-record JJAS daily state mean - 1",
    })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    filename, raw_bytes, gzip_bytes = dump_hashed(detail, "atlas-detail", args.output_dir)
    print(json.dumps({
        "detail": filename,
        "states": len(means_x10),
        "coverage": coverage,
        "uncompressed_bytes": raw_bytes,
        "compressed_bytes": gzip_bytes,
    }, indent=2))


if __name__ == "__main__":
    main()
