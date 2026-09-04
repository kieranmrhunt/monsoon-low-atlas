from __future__ import annotations

import json
import unittest
from pathlib import Path

from .gwl import crossing_windows


class GwlPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = json.loads(
            Path("data/cmip6-inventory/ipcc-ar6-gwl-crossings.json").read_text(encoding="utf-8")
        )

    def test_ssp245_two_degree_has_all_five_exact_members(self):
        records = crossing_windows(self.table, "ssp245", [2.0])
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record["status"] == "scenario-window" for record in records))
        self.assertEqual(
            {record["source_id"]: record["central_year"] for record in records},
            {
                "HadGEM3-GC31-LL": 2033,
                "MIROC6": 2073,
                "MPI-ESM1-2-HR": 2063,
                "MPI-ESM1-2-LR": 2057,
                "MRI-ESM2-0": 2049,
            },
        )

    def test_hadgem_one_point_five_requires_experiment_stitch(self):
        records = crossing_windows(
            self.table, "ssp245", [1.5], {"HadGEM3-GC31-LL"}
        )
        self.assertEqual(records[0]["start_year"], 2010)
        self.assertEqual(records[0]["end_year"], 2029)
        self.assertEqual(records[0]["status"], "requires-historical-scenario-stitch")


if __name__ == "__main__":
    unittest.main()
