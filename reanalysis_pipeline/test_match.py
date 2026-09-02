from __future__ import annotations

import unittest

import pandas as pd

from .match import match_tracks, normalise_tracks


class ReanalysisMatchTest(unittest.TestCase):
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
        self.assertTrue(any(record["source_track_id"] == "far" for record in rejected))


if __name__ == "__main__":
    unittest.main()
