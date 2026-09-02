#!/usr/bin/env python3
"""Shared grid and file helpers for alternative-reanalysis LPS tracking."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable

import numpy as np
import xarray as xr


EARTH_RADIUS_M = 6_371_008.8
TARGET_LONS = np.arange(45.0, 120.0 + 0.01, 1.0, dtype=np.float32)
TARGET_LATS = np.arange(-15.0, 45.0 + 0.01, 1.0, dtype=np.float32)
PRESSURE_LEVELS = np.asarray([850.0, 700.0, 500.0], dtype=np.float32)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_vorticity_x1e5(
    u: np.ndarray,
    v: np.ndarray,
    *,
    latitudes: np.ndarray = TARGET_LATS,
    longitudes: np.ndarray = TARGET_LONS,
) -> np.ndarray:
    """Return spherical relative vorticity in 10^-5 s^-1.

    The final two axes of ``u`` and ``v`` must be latitude and longitude.
    Unlike the lightweight forecast cross-check, this uses the full spherical
    expression, including the metric term in ``d(u cos(phi))/dphi``.
    """

    eastward = np.asarray(u, dtype=np.float64)
    northward = np.asarray(v, dtype=np.float64)
    if eastward.shape != northward.shape or eastward.ndim < 2:
        raise ValueError("u and v must have the same shape with latitude/longitude last")
    latitudes = np.asarray(latitudes, dtype=np.float64)
    longitudes = np.asarray(longitudes, dtype=np.float64)
    if eastward.shape[-2:] != (len(latitudes), len(longitudes)):
        raise ValueError("wind array does not match the supplied latitude/longitude axes")
    phi = np.deg2rad(latitudes)
    lam = np.deg2rad(longitudes)
    cos_phi = np.cos(phi)
    metric_shape = (1,) * (eastward.ndim - 2) + (len(phi), 1)
    cos_grid = cos_phi.reshape(metric_shape)
    dv_dlambda = np.gradient(northward, lam, axis=-1, edge_order=2)
    ducos_dphi = np.gradient(eastward * cos_grid, phi, axis=-2, edge_order=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        vorticity = (dv_dlambda - ducos_dphi) / (EARTH_RADIUS_M * cos_grid)
    return (vorticity * 1.0e5).astype(np.float32)


def target_grid(dataset: xr.Dataset | xr.DataArray) -> xr.Dataset | xr.DataArray:
    """Linearly sample a rectilinear source onto the atlas 1-degree grid."""

    rename = {}
    for source, target in (("lat", "latitude"), ("lon", "longitude"), ("lev", "level")):
        if source in dataset.dims or source in dataset.coords:
            if target not in dataset.dims and target not in dataset.coords:
                rename[source] = target
    value = dataset.rename(rename)
    if "latitude" not in value.coords or "longitude" not in value.coords:
        raise ValueError("dataset has no rectilinear latitude/longitude coordinates")
    if value.latitude.size > 1 and float(value.latitude[0]) > float(value.latitude[-1]):
        value = value.sortby("latitude")
    if value.longitude.size > 1 and float(value.longitude[0]) > float(value.longitude[-1]):
        value = value.sortby("longitude")
    return value.interp(
        latitude=xr.DataArray(TARGET_LATS, dims="latitude", coords={"latitude": TARGET_LATS}),
        longitude=xr.DataArray(TARGET_LONS, dims="longitude", coords={"longitude": TARGET_LONS}),
        method="linear",
    )


def atomic_to_netcdf(dataset: xr.Dataset, path: Path, *, complevel: int = 4) -> None:
    """Write a compact NetCDF atomically so interrupted tasks are resumable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".part-{os.getpid()}")
    encoding = {
        name: {"zlib": True, "complevel": complevel, "shuffle": True}
        for name in dataset.data_vars
        if np.issubdtype(dataset[name].dtype, np.number)
    }
    dataset.to_netcdf(temporary, engine="h5netcdf", encoding=encoding)
    os.replace(temporary, path)


def require_variables(dataset: xr.Dataset, variables: Iterable[str], path: Path) -> None:
    missing = [name for name in variables if name not in dataset]
    if missing:
        raise ValueError(f"{path} is missing variables: {', '.join(missing)}")

