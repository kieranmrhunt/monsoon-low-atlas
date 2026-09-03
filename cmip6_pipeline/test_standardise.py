from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import xarray as xr

from cmip6_pipeline.standardise import _hourly_precipitation, _select_levels


class StandardiseTest(unittest.TestCase):
    def test_pressure_levels_are_selected_and_converted(self) -> None:
        value = xr.DataArray(
            np.arange(4, dtype=np.float32)[:, None, None, None],
            dims=("plev", "time", "latitude", "longitude"),
            coords={
                "plev": xr.DataArray([50000.0, 70000.0, 85000.0, 92500.0], dims="plev", attrs={"units": "Pa"}),
                "time": [pd.Timestamp("2000-01-01")],
                "latitude": [0.0],
                "longitude": [80.0],
            },
            name="ua",
        ).rename({"plev": "level"})
        selected = _select_levels(value)
        np.testing.assert_allclose(selected.level.values, [850.0, 700.0, 500.0])

    def test_precipitation_interval_means_are_not_smoothed(self) -> None:
        native = pd.date_range("2000-07-01T01:30", periods=3, freq="3h")
        value = xr.DataArray(
            np.asarray([1.0, 2.0, 3.0], dtype=np.float32)[:, None, None],
            dims=("time", "latitude", "longitude"),
            coords={"time": native, "latitude": [0.0], "longitude": [80.0]},
            attrs={"units": "kg m-2 s-1"},
        )
        hourly = pd.date_range("2000-07-01", periods=9, freq="h")
        sampled = _hourly_precipitation(value, hourly)
        np.testing.assert_allclose(sampled[:, 0, 0], [1, 1, 1, 2, 2, 2, 3, 3, 3])


if __name__ == "__main__":
    unittest.main()
