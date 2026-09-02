from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import xarray as xr

from .standardise_merra2 import label_precipitation_at_interval_end, month_bounds


class Merra2StandardisationTest(unittest.TestCase):
    def test_month_bounds_are_end_exclusive(self) -> None:
        start, end = month_bounds("201602")
        self.assertEqual(start, pd.Timestamp("2016-02-01"))
        self.assertEqual(end, pd.Timestamp("2016-03-01"))
        self.assertEqual(len(pd.date_range(start, end - pd.Timedelta(hours=1), freq="h")), 696)

    def test_precipitation_midpoints_are_labelled_at_interval_end(self) -> None:
        values = xr.DataArray(
            np.asarray([1.0, 2.0], dtype=np.float32),
            dims="time",
            coords={"time": pd.to_datetime(["2016-07-01T00:30", "2016-07-01T01:30"])},
        )
        shifted = label_precipitation_at_interval_end(values)
        actual = pd.DatetimeIndex(pd.to_datetime(shifted.time.values))
        self.assertTrue(actual.equals(pd.DatetimeIndex(["2016-07-01T01:00", "2016-07-01T02:00"])))
        np.testing.assert_array_equal(shifted.values, values.values)


if __name__ == "__main__":
    unittest.main()
