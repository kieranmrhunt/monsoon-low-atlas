from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import xarray as xr

from .standardise_merra2 import (
    label_precipitation_at_interval_end,
    month_bounds,
    resolve_precipitation_file,
)


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

    def test_missing_raw_precipitation_is_not_returned_as_a_candidate(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(FileNotFoundError, "1979-12-31"):
                resolve_precipitation_file(root / "local", date(1979, 12, 31), raw_root=root / "raw")

    def test_existing_raw_precipitation_takes_priority(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw" / "raw" / "precipitation" / "1980" / "merra2-precipitation-19800101.nc4"
            local = root / "local" / "precip"
            raw.parent.mkdir(parents=True)
            local.mkdir(parents=True)
            raw.touch()
            (local / "MERRA2-19800101.nc4").touch()
            self.assertEqual(resolve_precipitation_file(root / "local", date(1980, 1, 1), raw_root=root / "raw"), raw)


if __name__ == "__main__":
    unittest.main()
