from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from .parallel_link import assign_continuous_track_ids, merge, prepare, validate_source_label


class ParallelReanalysisLinkTest(unittest.TestCase):
    def test_cmip6_source_labels_are_safe_but_not_hardcoded(self) -> None:
        label = "MPI-ESM1-2-HR_historical_r1i1p1f1_gn"
        self.assertEqual(validate_source_label(label), label)
        with self.assertRaises(ValueError):
            validate_source_label("../../not-safe")

    def test_prepare_adds_month_halos_and_merge_reconciles_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "tracking"
            candidates = output_root / "candidates"
            candidates.mkdir(parents=True)
            for month in ("200012", "200101"):
                pd.DataFrame({
                    "candidate_uid": [f"{month}-0", f"{month}-1"],
                    "time": [
                        f"{month[:4]}-{month[4:]}-01T00:00:00",
                        f"{month[:4]}-{month[4:]}-01T06:00:00",
                    ],
                    "frame": [0, 6],
                    "lon": [80.0, 80.2],
                    "lat": [20.0, 20.1],
                    "centre_score": [2.0, 2.1],
                }).to_csv(candidates / f"candidates-{month}.csv", index=False)
            run_root = root / "run"
            manifest = prepare("merra2", output_root, run_root)
            self.assertEqual(prepare("merra2", output_root, run_root), manifest)
            tasks = pd.read_csv(manifest)
            self.assertEqual(tasks["core_year"].tolist(), [2000, 2001])
            self.assertEqual(tasks["month_count"].tolist(), [2, 2])

            shared = [
                {
                    "candidate_uid": "shared-dec",
                    "time": "2000-12-31T23:00:00",
                    "track_id": 1,
                    "position_source": "observed",
                    "lon": 80.0,
                    "lat": 20.0,
                    "lon_smooth": 80.0,
                    "lat_smooth": 20.0,
                },
                {
                    "candidate_uid": "shared-jan",
                    "time": "2001-01-01T00:00:00",
                    "track_id": 1,
                    "position_source": "observed",
                    "lon": 80.2,
                    "lat": 20.1,
                    "lon_smooth": 80.2,
                    "lat_smooth": 20.1,
                },
            ]
            for record in tasks.to_dict("records"):
                year = int(record["core_year"])
                task_dir = Path(record["task_dir"])
                task_dir.mkdir(parents=True, exist_ok=True)
                frame = pd.DataFrame(shared)
                frame["track_id"] = 1 if year == 2000 else 7
                tracks = Path(record["tracks"])
                frame.to_csv(tracks, index=False)
                from .common import sha256

                (task_dir / "status.json").write_text(json.dumps({
                    "status": "complete",
                    "tracks_sha256": sha256(tracks),
                }))

            linked = merge("merra2", output_root, run_root)
            result = pd.read_csv(linked)
            self.assertEqual(len(result), 2)
            self.assertEqual(result["track_id"].nunique(), 1)
            self.assertEqual(result["parallel_block_year"].tolist(), [2000, 2001])
            summary = json.loads((output_root / "merra2-parallel-link-summary.json").read_text())
            self.assertEqual(summary["accepted_tracks"], 1)
            self.assertEqual(summary["reconciliation"][0]["selected_one_to_one_pairs"], 1)

    def test_reconciled_identity_splits_at_non_hourly_boundary(self) -> None:
        frame = pd.DataFrame(
            {
                "time": ["2000-12-31T23:00:00", "2001-01-01T18:00:00"],
                "lon": [80.0, 81.0],
                "lat": [20.0, 20.5],
            }
        )
        times = pd.to_datetime(frame.time, utc=True)
        roots = pd.Series(["2000:1", "2000:1"], index=frame.index)
        assigned, next_id, audit = assign_continuous_track_ids(
            frame,
            times,
            roots,
            {"2000:1": 4},
            {},
            5,
            block_year=2001,
        )
        self.assertEqual(assigned.tolist(), [4, 5])
        self.assertEqual(next_id, 6)
        self.assertEqual(audit[0]["reason"], "non_hourly_boundary")
        self.assertEqual(audit[0]["elapsed_hours"], 19.0)

    def test_reconciled_identity_splits_at_excessive_hourly_jump(self) -> None:
        frame = pd.DataFrame(
            {
                "time": ["2000-12-31T23:00:00", "2001-01-01T00:00:00"],
                "lon": [80.0, 85.0],
                "lat": [20.0, 20.0],
            }
        )
        times = pd.to_datetime(frame.time, utc=True)
        roots = pd.Series(["2000:1", "2000:1"], index=frame.index)
        assigned, _next_id, audit = assign_continuous_track_ids(
            frame,
            times,
            roots,
            {"2000:1": 2},
            {},
            3,
            block_year=2001,
        )
        self.assertEqual(assigned.tolist(), [2, 3])
        self.assertEqual(audit[0]["reason"], "excessive_boundary_displacement")


if __name__ == "__main__":
    unittest.main()
