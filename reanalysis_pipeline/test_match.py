from __future__ import annotations

import json
import unittest

import pandas as pd

from .match import match_tracks, normalise_tracks


class ReanalysisMatchTest(unittest.TestCase):
    @staticmethod
    def physical_rows(track_id: str, times: pd.DatetimeIndex, longitudes: list[float]) -> list[dict[str, object]]:
        return [
            {
                "track_id": track_id,
                "time": time,
                "lon_smooth": longitude,
                "lat_smooth": 20.0,
                "lon_detected": longitude,
                "lat_detected": 20.0,
                "position_source": "observed",
                "imd_category_raw": 1,
                "candidate_quality": 7.0,
                "centre_score": 7.0,
                "max_vort_smoothed": 8.0,
                "pressure_deficit_hpa": 4.0,
                "heat_low_score": 0.2,
            }
            for time, longitude in zip(times, longitudes, strict=True)
        ]

    def test_nearby_track_matches_and_remote_track_does_not(self) -> None:
        times = pd.date_range("2016-07-01", periods=24, freq="h")
        era = normalise_tracks(pd.DataFrame({
            "track_id": [10] * 24,
            "time": times,
            "lon": [80.0 + index * 0.05 for index in range(24)],
            "lat": [20.0] * 24,
        }), source="ERA5")
        alternative = normalise_tracks(pd.DataFrame({
            "track_id": ["near"] * 24 + ["far"] * 24,
            "time": list(times) + list(times),
            "lon_smooth": [80.2 + index * 0.05 for index in range(24)] + [100.0] * 24,
            "lat_smooth": [20.1] * 24 + [5.0] * 24,
        }), source="MERRA-2")
        selected, rejected = match_tracks(alternative, era)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_track_id"], "near")
        self.assertEqual(selected[0]["era5_track_id"], 10)
        self.assertIsNone(selected[0]["second_best_score_margin"])
        json.dumps({"selected": selected, "rejected": rejected}, allow_nan=False)
        self.assertTrue(any(record["source_track_id"] == "far" for record in rejected))

    def test_mixed_second_best_margins_remain_strict_json(self) -> None:
        times = pd.date_range("2016-07-01", periods=24, freq="h")
        era = normalise_tracks(pd.DataFrame({
            "track_id": [10] * 24 + [20] * 24 + [30] * 24,
            "time": list(times) * 3,
            "lon": [80.0] * 24 + [82.0] * 24 + [90.0] * 24,
            "lat": [20.0] * 72,
        }), source="ERA5")
        alternative = normalise_tracks(pd.DataFrame({
            "track_id": ["two-candidates"] * 24 + ["one-candidate"] * 24,
            "time": list(times) * 2,
            "lon_smooth": [80.0] * 24 + [90.0] * 24,
            "lat_smooth": [20.0] * 48,
        }), source="JRA-55")
        selected, rejected = match_tracks(alternative, era)
        self.assertEqual(len(selected), 2)
        self.assertTrue(any(record["second_best_score_margin"] is not None for record in selected))
        self.assertTrue(any(record["second_best_score_margin"] is None for record in selected))
        json.dumps({"selected": selected, "rejected": rejected}, allow_nan=False)

    def test_complete_physical_track_beats_short_close_fragment(self) -> None:
        times = pd.date_range("2016-07-01", periods=100, freq="h")
        era = normalise_tracks(pd.DataFrame({
            "track_id": [10] * len(times),
            "time": times,
            "lon": [80.0] * len(times),
            "lat": [20.0] * len(times),
        }), source="ERA5")
        # Twenty initially divergent hours deliberately lift p90 above the
        # normal gate, while the remaining 80% closely follow the ERA5 event.
        full = self.physical_rows("full", times, [86.5] * 20 + [80.1] * 80)
        short_times = times[40:64]
        short = self.physical_rows("fragment", short_times, [80.0] * len(short_times))
        alternative = normalise_tracks(pd.DataFrame(full + short), source="MERRA-2")

        selected, rejected = match_tracks(alternative, era)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_track_id"], "full")
        self.assertTrue(selected[0]["source_physical_event"])
        self.assertEqual(selected[0]["eligibility_basis"], "coverage_recovery")
        self.assertTrue(any(record["source_track_id"] == "fragment" for record in rejected))


if __name__ == "__main__":
    unittest.main()
