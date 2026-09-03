from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from .native import NATIVE_INDEX_SCHEMA, NATIVE_MONTH_SCHEMA, build_native_archive


class NativeReanalysisArchiveTest(unittest.TestCase):
    @staticmethod
    def row(track_id: int, time: pd.Timestamp, lon: float, lat: float, **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "track_id": track_id,
            "time": time,
            "lon_smooth": lon,
            "lat_smooth": lat,
            "lon_detected": lon,
            "lat_detected": lat,
            "position_source": "observed",
            "reject_reason": "accepted",
            "imd_category_raw": 1,
            "candidate_quality": 7.0,
            "centre_score": 7.0,
            "max_vort_smoothed": 8.0,
            "pressure_deficit_hpa": 4.0,
            "heat_low_score": 0.2,
        }
        value.update(changes)
        return value

    def test_tracks_are_partitioned_by_active_month_with_full_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            linked = root / "linked.csv"
            track_one_times = pd.date_range("2016-07-31 00:00", periods=32, freq="h")
            track_two_times = pd.date_range("2016-08-02 06:00", periods=31, freq="h")
            rows = [self.row(1, time, 80.0 + index * 0.02, 20.0) for index, time in enumerate(track_one_times)]
            rows[-1]["position_source"] = "interpolated"
            rows.extend(self.row(2, time, 75.0 + index * 0.02, 18.0) for index, time in enumerate(track_two_times))
            rows.extend(self.row(9, time, 70.0, 17.0, pressure_deficit_hpa=1.0) for time in track_two_times)
            pd.DataFrame(rows).to_csv(linked, index=False)
            index_path = build_native_archive("jra55", linked, root / "native", chunksize=7)
            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["schema"], NATIVE_INDEX_SCHEMA)
            self.assertEqual(index["track_count"], 2)
            self.assertEqual(index["linker_track_count"], 3)
            self.assertEqual(index["rejected_linker_track_count"], 1)
            self.assertEqual(index["coverage_start_utc"], "2016-07-31T00:00:00Z")
            self.assertEqual(index["coverage_end_utc"], "2016-08-03T12:00:00Z")
            self.assertEqual(set(index["months"]), {"201607", "201608"})
            with gzip.open(root / "native" / "201607.json.gz", "rt", encoding="utf-8") as stream:
                july = json.load(stream)
            with gzip.open(root / "native" / "201608.json.gz", "rt", encoding="utf-8") as stream:
                august = json.load(stream)
            self.assertEqual(july["schema"], NATIVE_MONTH_SCHEMA)
            self.assertEqual(set(july["tracks"]), {"1"})
            self.assertEqual(set(august["tracks"]), {"1", "2"})
            self.assertEqual(july["tracks"]["1"], august["tracks"]["1"])
            self.assertEqual(july["tracks"]["1"][-1][-1], "i")


if __name__ == "__main__":
    unittest.main()
