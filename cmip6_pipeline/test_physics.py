from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cmip6_pipeline.model_calendar import time_axis
from cmip6_pipeline.physics import (
    GAP_COMPLETENESS_COLUMN,
    classify_intensity,
    count_closed_isobars,
    drop_unobserved_track_fragments,
    persistent_category,
    require_complete_gap_blocks,
    restore_native_time,
)


class PhysicsTest(unittest.TestCase):
    def test_native_genesis_filter_keeps_complete_boundary_crossing_track(self) -> None:
        axis = time_axis("360_day", "201012")
        frame = pd.DataFrame(
            {
                "track_id": [1, 1, 2],
                "time": pd.to_datetime(
                    ["2010-12-30T00:00", "2010-12-31T00:00", "2010-12-31T06:00"]
                ),
            }
        )
        result, summary = restore_native_time(
            frame,
            {
                "core_start": "201012",
                "core_end": "201012",
                "native_core_start": "201012",
                "native_core_end": "201012",
                "time_axis": axis.record(),
            },
        )
        self.assertEqual(result.track_id.tolist(), [1, 1])
        self.assertEqual(result.model_time.tolist(), [
            "2010-12-30T00:00:00",
            "2011-01-01T00:00:00",
        ])
        self.assertEqual(summary["tracks_removed_outside_native_genesis_window"], 1)

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

    def test_incomplete_physics_rejects_the_whole_interpolated_bridge(self) -> None:
        frame = pd.DataFrame(
            {
                "track_id": [1] * 5 + [2] * 4,
                "time": pd.date_range("2000-01-01", periods=9, freq="h"),
                "position_source": [
                    "observed",
                    "interpolated",
                    "interpolated",
                    "interpolated",
                    "observed",
                    "observed",
                    "interpolated",
                    "interpolated",
                    "observed",
                ],
                "physics_complete_v54rean": [
                    True,
                    True,
                    False,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                ],
            }
        )
        result, summary = require_complete_gap_blocks(frame)
        self.assertEqual(
            result[GAP_COMPLETENESS_COLUMN].tolist(),
            [True, False, False, False, True, True, True, True, True],
        )
        self.assertEqual(summary["gap_blocks"], 2)
        self.assertEqual(summary["incomplete_gap_blocks_rejected"], 1)
        self.assertEqual(summary["gap_rows_in_rejected_blocks"], 3)

    def test_continuity_split_drops_observation_free_fragment(self) -> None:
        frame = pd.DataFrame(
            {
                "track_id": [1, 1, 2, 2, 3],
                "position_source": [
                    "observed",
                    "interpolated",
                    "interpolated",
                    "interpolated",
                    "observed",
                ],
            }
        )
        result, summary = drop_unobserved_track_fragments(frame)
        self.assertEqual(result.track_id.tolist(), [1, 1, 3])
        self.assertEqual(summary["unobserved_fragments_removed"], 1)
        self.assertEqual(summary["unobserved_rows_removed"], 2)


if __name__ == "__main__":
    unittest.main()
