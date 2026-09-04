import unittest
import tempfile
from pathlib import Path

import numpy as np
import xarray as xr

from reanalysis_pipeline.common import TARGET_LATS, TARGET_LONS

from .standardise_era5 import (
    STANDARD_GRAVITY_MS2,
    STANDARD_LAPSE_PRESSURE_COEFFICIENT_M1,
    STANDARD_LAPSE_PRESSURE_EXPONENT,
    _normalise,
    _sample_exact_nodes,
    estimated_surface_pressure,
)


class Era5CommonGridTests(unittest.TestCase):
    def test_normalises_era5_coordinate_names(self):
        source = xr.Dataset(
            {"u": (("valid_time", "pressure_level", "latitude", "longitude"), np.zeros((1, 1, 1, 1)))},
            coords={"valid_time": [0], "pressure_level": [850], "latitude": [0], "longitude": [45]},
        )
        result = _normalise(source)
        self.assertEqual(result.u.dims, ("time", "level", "latitude", "longitude"))

    def test_exact_node_sampling_preserves_target_values(self):
        latitudes = np.arange(-15, 46, .25)
        longitudes = np.arange(45, 121, .25)
        values = latitudes[:, None] * 1000 + longitudes[None, :]
        source = xr.DataArray(
            values,
            dims=("latitude", "longitude"),
            coords={"latitude": latitudes, "longitude": longitudes},
            name="field",
        )
        result = _sample_exact_nodes(source)
        self.assertTrue(np.array_equal(result.latitude.values, TARGET_LATS))
        self.assertTrue(np.array_equal(result.longitude.values, TARGET_LONS))
        self.assertEqual(float(result.sel(latitude=20, longitude=80)), 20080.0)

    def test_surface_pressure_estimate_uses_fixed_orography(self):
        msl = xr.DataArray(
            np.full((2, len(TARGET_LATS), len(TARGET_LONS)), 100_000.0, dtype=np.float32),
            dims=("time", "latitude", "longitude"),
            coords={"time": [0, 1], "latitude": TARGET_LATS, "longitude": TARGET_LONS},
            name="msl",
        )
        height_m = 1000.0
        static = xr.Dataset(
            {
                "z": (
                    ("latitude", "longitude"),
                    np.full(
                        (len(TARGET_LATS), len(TARGET_LONS)),
                        height_m * STANDARD_GRAVITY_MS2,
                        dtype=np.float32,
                    ),
                    {"units": "m**2 s**-2"},
                )
            },
            coords={"latitude": TARGET_LATS, "longitude": TARGET_LONS},
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "static.nc"
            static.to_netcdf(path)
            result = estimated_surface_pressure(msl, path)
        expected = 100_000.0 * (
            1.0 - STANDARD_LAPSE_PRESSURE_COEFFICIENT_M1 * height_m
        ) ** STANDARD_LAPSE_PRESSURE_EXPONENT
        self.assertEqual(result.shape, msl.shape)
        self.assertTrue(np.allclose(result, expected, rtol=1e-6))
        self.assertEqual(result.attrs["units"], "Pa")


if __name__ == "__main__":
    unittest.main()
