#!/usr/bin/env python3
"""Measure whether WeatherBench Zarr reads are practical from JASMIN."""

from __future__ import annotations

import time

import gcsfs
import xarray as xr


STORE = (
    "weatherbench2/datasets/ifs_ens/"
    "2018-2022-240x121_equiangular_with_poles_conservative.zarr"
)


def main() -> None:
    started = time.monotonic()
    filesystem = gcsfs.GCSFileSystem(token="anon")
    dataset = xr.open_zarr(filesystem.get_mapper(STORE), consolidated=True)
    print(f"metadata_seconds={time.monotonic() - started:.2f}", flush=True)
    print(f"sizes={dict(dataset.sizes)}", flush=True)
    print(
        f"latitude={float(dataset.latitude.values[0])},{float(dataset.latitude.values[-1])} "
        f"longitude={float(dataset.longitude.values[0])},{float(dataset.longitude.values[-1])}",
        flush=True,
    )
    subset = (
        dataset[["mean_sea_level_pressure"]]
        .sel(time="2018-07-01T00:00:00")
        .isel(number=0, prediction_timedelta=slice(0, 8))
        .sel(longitude=slice(45, 120), latitude=slice(-15, 45))
        .load()
    )
    print(f"one_surface_chunk_seconds={time.monotonic() - started:.2f}", flush=True)
    print(f"subset_sizes={dict(subset.sizes)} mean_pa={float(subset.mean_sea_level_pressure.mean()):.2f}")


if __name__ == "__main__":
    main()
