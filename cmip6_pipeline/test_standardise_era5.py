import unittest

import numpy as np
import xarray as xr

from reanalysis_pipeline.common import TARGET_LATS, TARGET_LONS

from .standardise_era5 import _normalise, _sample_exact_nodes


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


if __name__ == "__main__":
    unittest.main()
