from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cmip6_pipeline.physics import classify_intensity, count_closed_isobars, persistent_category


class PhysicsTest(unittest.TestCase):
    def test_closed_isobar_count_stops_at_open_component(self) -> None:
        yy, xx = np.mgrid[-5:6, -5:6]
        field = 998.0 + np.hypot(xx, yy)
        count, level, size = count_closed_isobars(field, 5, 5)
        self.assertGreaterEqual(count, 2)
        self.assertTrue(np.isfinite(level))
        self.assertGreater(size, 0)

    def test_six_hour_persistence_demotes_short_depression(self) -> None:
        raw = [1, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2]
        result = persistent_category(raw, 6, np.arange(len(raw)))
        self.assertTrue(np.all(result[1:6] == 1))
        self.assertTrue(np.all(result[7:] == 2))

    def test_land_closed_isobars_supply_only_a_depression_floor(self) -> None:
        frame = pd.DataFrame(
            {
                "track_id": [1] * 6,
                "time": pd.date_range("2000-01-01", periods=6, freq="h"),
                "p95_anomaly_wind_125km_ms": [9.0] * 6,
                "land_fraction": [1.0] * 6,
                "closed_isobars_2hpa_actual": [2] * 6,
            }
        )
        result, summary = classify_intensity(frame)
        self.assertTrue(result.imd_category.eq(2).all())
        self.assertEqual(summary["event_peak_categories"], {"2": 1})


if __name__ == "__main__":
    unittest.main()
