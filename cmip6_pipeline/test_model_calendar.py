from __future__ import annotations

import unittest

import cftime
import pandas as pd

from cmip6_pipeline.model_calendar import native_iso, time_axis


class TimeAxisTest(unittest.TestCase):
    def test_gregorian_clock_is_identity_including_leap_day(self) -> None:
        axis = time_axis("proleptic_gregorian", "199901")
        native = [
            cftime.DatetimeProlepticGregorian(2000, 2, 29, 23),
            cftime.DatetimeProlepticGregorian(2000, 3, 1, 0),
        ]
        analysis = axis.native_to_analysis(native)
        self.assertEqual(analysis[0], pd.Timestamp("2000-02-29T23:00:00"))
        self.assertEqual(analysis[1], pd.Timestamp("2000-03-01T00:00:00"))
        self.assertEqual(analysis[1] - analysis[0], pd.Timedelta(hours=1))

    def test_noleap_clock_keeps_elapsed_time_and_native_identity(self) -> None:
        axis = time_axis("noleap", "199901")
        native = [
            cftime.DatetimeNoLeap(2000, 2, 28, 23),
            cftime.DatetimeNoLeap(2000, 3, 1, 0),
        ]
        analysis = axis.native_to_analysis(native)
        self.assertEqual(analysis[0], pd.Timestamp("2000-02-28T23:00:00"))
        self.assertEqual(analysis[1], pd.Timestamp("2000-02-29T00:00:00"))
        self.assertEqual(analysis[1] - analysis[0], pd.Timedelta(hours=1))
        restored = axis.analysis_to_native(analysis)
        self.assertEqual(
            [native_iso(value) for value in restored],
            [native_iso(value) for value in native],
        )

    def test_360_day_clock_is_hourly_across_non_civil_dates(self) -> None:
        axis = time_axis("360_day", "198101")
        native = [
            cftime.Datetime360Day(1981, 1, 30, 23),
            cftime.Datetime360Day(1981, 2, 1, 0),
            cftime.Datetime360Day(1981, 2, 30, 0),
        ]
        analysis = axis.native_to_analysis(native)
        self.assertEqual(analysis[1] - analysis[0], pd.Timedelta(hours=1))
        self.assertEqual(analysis[1], pd.Timestamp("1981-01-31T00:00:00"))
        restored = axis.analysis_to_native(analysis)
        self.assertEqual([native_iso(value) for value in restored], [native_iso(value) for value in native])

    def test_360_day_thirty_year_window_retains_exact_elapsed_hours(self) -> None:
        axis = time_axis("360_day", "198101")
        start, end = axis.analysis_interval_for_native_months("198101", "201012")
        self.assertEqual(start, pd.Timestamp("1981-01-01T00:00:00"))
        self.assertEqual(end - start, pd.Timedelta(days=30 * 360))

    def test_annotation_keeps_analysis_and_native_identity_separate(self) -> None:
        axis = time_axis("360_day", "198101")
        frame = pd.DataFrame({"time": [pd.Timestamp("1981-03-01T00:00:00")]})
        result = axis.annotate(frame)
        self.assertEqual(result.loc[0, "model_time"], "1981-02-30T00:00:00")
        self.assertEqual(int(result.loc[0, "model_month"]), 2)
        self.assertIn("analysis_clock", result.loc[0, "time_basis"])


if __name__ == "__main__":
    unittest.main()
