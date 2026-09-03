from __future__ import annotations

import unittest

import pandas as pd

from .selection import physical_track_passes, select_physical_tracks


def track(track_id: int, *, pressure: float = 4.0, hours: int = 31) -> pd.DataFrame:
    return pd.DataFrame({
        "track_id": [track_id] * hours,
        "time": pd.date_range("2016-07-01", periods=hours, freq="h"),
        "lon_detected": [80.0] * hours,
        "lat_detected": [20.0] * hours,
        "position_source": ["observed"] * hours,
        "imd_category_raw": [1] * hours,
        "candidate_quality": [7.0] * hours,
        "centre_score": [7.0] * hours,
        "max_vort_smoothed": [8.0] * hours,
        "pressure_deficit_hpa": [pressure] * hours,
        "heat_low_score": [0.2] * hours,
    })


class SourceNativePhysicalSelectionTest(unittest.TestCase):
    def test_gate_retains_supported_track_and_rejects_weak_track(self) -> None:
        strong = track(1)
        weak = track(2, pressure=1.0)
        self.assertTrue(physical_track_passes(strong))
        self.assertFalse(physical_track_passes(weak))
        selected, summary = select_physical_tracks(pd.concat([strong, weak], ignore_index=True))
        self.assertEqual(set(selected["track_id"]), {1})
        self.assertEqual(summary["linker_tracks"], 2)
        self.assertEqual(summary["selected_tracks"], 1)

    def test_duration_is_observed_span_not_row_count(self) -> None:
        short = track(3, hours=30)
        self.assertFalse(physical_track_passes(short))


if __name__ == "__main__":
    unittest.main()
