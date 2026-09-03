#!/usr/bin/env python3
"""Build the common 1-degree terrain and land mask used by CMIP6 tracking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xarray as xr

from reanalysis_pipeline.common import TARGET_LATS, TARGET_LONS, atomic_to_netcdf, sha256, target_grid


def build(source: Path, output: Path) -> dict[str, object]:
    with xr.open_dataset(source) as dataset:
        missing = sorted({"z", "lsm"} - set(dataset.data_vars))
        if missing:
            raise ValueError(f"{source} lacks {missing}")
        sampled = target_grid(dataset[["z", "lsm"]]).astype(np.float32).load()
    sampled.attrs.update(
        {
            "title": "Common 1-degree surface fields for CMIP6 LPS tracking",
            "source": "ERA5 invariant surface geopotential and land-sea mask",
            "processing": "exact-grid sampling from the ERA5 0.25-degree invariant grid",
            "purpose": "Hold land/terrain geography fixed across models and experiments",
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_netcdf(sampled, output)
    with xr.open_dataset(output) as check:
        if not np.array_equal(check.latitude.values, TARGET_LATS):
            raise ValueError("static latitude axis differs from the shared grid")
        if not np.array_equal(check.longitude.values, TARGET_LONS):
            raise ValueError("static longitude axis differs from the shared grid")
        if not np.isfinite(check[["z", "lsm"]].to_array().values).all():
            raise ValueError("static fields contain non-finite values")
    return {
        "schema": "lps-atlas-cmip6-common-static-v1",
        "source": str(source.resolve()),
        "source_sha256": sha256(source),
        "output": str(output.resolve()),
        "output_sha256": sha256(output),
        "shape": [len(TARGET_LATS), len(TARGET_LONS)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
