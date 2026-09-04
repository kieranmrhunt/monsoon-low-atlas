from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cmip6_pipeline.source import (
    RunSpec,
    files_overlapping,
    files_overlapping_stamps,
    period,
    period_stamps,
)


class SourceTest(unittest.TestCase):
    def test_native_stamp_overlap_accepts_360_day_february_30(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "ua_model_198102010000-198102300000.nc"
            second = directory / "ua_model_198103010000-198103300000.nc"
            first.touch()
            second.touch()
            self.assertEqual(period_stamps(first), ("19810201000000", "19810230000099"))
            selected = files_overlapping_stamps(
                directory,
                "19810229120000",
                "19810301060000",
            )
            self.assertEqual(selected, [first, second])

    def test_run_slug_is_stable(self) -> None:
        spec = RunSpec("ScenarioMIP", "DKRZ", "MPI-ESM1-2-HR", "ssp245", "r1i1p1f1")
        self.assertEqual(spec.slug, "MPI-ESM1-2-HR_ssp245_r1i1p1f1_gn")

    def test_period_and_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory / "ua_6hrPlevPt_M_historical_r1_gn_198501010600-199001010000.nc"
            second = directory / "ua_6hrPlevPt_M_historical_r1_gn_199001010600-199501010000.nc"
            first.touch()
            second.touch()
            self.assertEqual(period(second)[0], pd.Timestamp("1990-01-01T06:00"))
            selected = files_overlapping(directory, pd.Timestamp("1990-07-01"), pd.Timestamp("1990-08-01"))
            self.assertEqual(selected, [second])


if __name__ == "__main__":
    unittest.main()
