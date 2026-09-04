from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import cftime
import numpy as np
import pandas as pd
import xarray as xr

from cmip6_pipeline.model_calendar import time_axis
from cmip6_pipeline.source import RunSpec
from cmip6_pipeline.standardise import (
    _hourly_precipitation,
    _open_variable,
    _select_levels,
    field_table,
)


class StandardiseTest(unittest.TestCase):
    def test_360_day_source_is_selected_on_the_continuous_analysis_clock(self) -> None:
        spec = RunSpec("CMIP", "MOHC", "HadGEM3-GC31-LL", "historical", "r1i1p1f3", "gn")
        axis = time_axis("360_day", "198101")
        native = [
            cftime.Datetime360Day(1981, 2, 30, 0),
            cftime.Datetime360Day(1981, 2, 30, 6),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = (
                root
                / "CMIP/MOHC/HadGEM3-GC31-LL/historical/r1i1p1f3/6hrPlevPt/ua/gn/latest"
            )
            directory.mkdir(parents=True)
            path = directory / "ua_test_198102300000-198102300600.nc"
            xr.DataArray(
                np.ones((2, 3, 1, 1), dtype=np.float32),
                dims=("time", "plev", "lat", "lon"),
                coords={
                    "time": native,
                    "plev": xr.DataArray(
                        [85000.0, 70000.0, 50000.0],
                        dims="plev",
                        attrs={"units": "Pa"},
                    ),
                    "lat": [0.0],
                    "lon": [80.0],
                },
                name="ua",
            ).to_dataset().to_netcdf(path)
            analysis = axis.native_to_analysis(native)
            value, paths = _open_variable(
                root,
                spec,
                "ua",
                analysis[0],
                analysis[-1],
                pressure_levels=True,
                time_axis=axis,
            )
        self.assertEqual(paths, [path])
        self.assertTrue(pd.DatetimeIndex(value.time.values).equals(analysis))

    def test_hadgem_pressure_table_is_consistent_across_experiments(self) -> None:
        for experiment in ("historical", "ssp245"):
            spec = RunSpec("CMIP", "MOHC", "HadGEM3-GC31-LL", experiment, "r1i1p1f3")
            self.assertEqual(field_table(spec, "psl"), "6hrPlev")

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
