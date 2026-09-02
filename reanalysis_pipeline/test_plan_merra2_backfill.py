from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from .plan_merra2_backfill import plan


class Merra2BackfillPlanTest(unittest.TestCase):
    def test_range_has_previous_precipitation_and_next_analysis_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = plan(date(2016, 7, 1), date(2016, 8, 1), Path(directory), 40)
            self.assertEqual(value["month_count"], 2)
            self.assertEqual(value["first_download_day"], "2016-06-30")
            self.assertEqual(value["last_download_day"], "2016-09-01")
            self.assertEqual(len(value["chunks"]), 2)

    def test_first_dataset_month_does_not_request_1979(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = plan(date(1980, 1, 1), date(1980, 1, 1), Path(directory), 900)
            self.assertEqual(value["first_download_day"], "1980-01-01")
            self.assertEqual(value["last_download_day"], "1980-02-01")


if __name__ == "__main__":
    unittest.main()
