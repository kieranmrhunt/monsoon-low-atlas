from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from .standardise_era_interim import analysis_path, forecast_path, interpolation_source_times


class EraInterimStandardisationTest(unittest.TestCase):
    def test_archive_paths(self) -> None:
        root = Path("/archive")
        stamp = pd.Timestamp("2016-07-01T06:00")
        self.assertEqual(
            analysis_path(root, "pressure", stamp),
            Path("/archive/gg/ap/2016/07/01/ggap201607010600.nc"),
        )
        self.assertEqual(
            forecast_path(root, pd.Timestamp("2016-07-01T00:00"), 3),
            Path("/archive/ga/fs/2016/07/01/gafs201607010003.nc"),
        )

    def test_final_month_stops_at_last_analysis(self) -> None:
        actual = interpolation_source_times(pd.Timestamp("2019-08-01"), pd.Timestamp("2019-09-01"))
        self.assertEqual(actual[-1], pd.Timestamp("2019-08-31T18:00"))


if __name__ == "__main__":
    unittest.main()
