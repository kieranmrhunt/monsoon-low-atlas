from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cmip6_pipeline.plan import PRESETS, PeriodPlan, build_plan
from cmip6_pipeline.source import RunSpec


class PlanTest(unittest.TestCase):
    def test_next_wave_canaries_are_small_like_for_like_pairs(self) -> None:
        for name in ("miroc6-canary", "mpi-lr-canary", "mri-canary"):
            periods = PRESETS[name]()
            self.assertEqual(len(periods), 2)
            self.assertEqual(periods[0].spec.source_id, periods[1].spec.source_id)
            self.assertEqual(periods[0].spec.member_id, periods[1].spec.member_id)
            self.assertEqual((periods[0].core_start, periods[0].core_end), ("199006", "199009"))
            self.assertEqual((periods[1].core_start, periods[1].core_end), ("208006", "208009"))

    def test_mri_full_pair_uses_complete_late_century_boundary(self) -> None:
        periods = PRESETS["mri-paired"]()
        self.assertEqual((periods[0].core_start, periods[0].core_end), ("198101", "201012"))
        self.assertEqual((periods[1].core_start, periods[1].core_end), ("207001", "209912"))

    @patch("cmip6_pipeline.plan._verify_source_month")
    def test_full_and_boundary_halos_are_explicit(self, _verify: object) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            static = root / "static.nc"
            static.write_bytes(b"fixed")
            periods = [
                PeriodPlan(RunSpec("CMIP", "I", "M", "historical", "r1", "gn"), "200001", "200012", "full"),
                PeriodPlan(RunSpec("ScenarioMIP", "I", "M", "ssp245", "r1", "gn"), "209901", "209912", "boundary"),
            ]
            manifest = build_plan(root / "run", periods, badc_root=root / "badc", static_file=static)
            self.assertTrue(manifest.is_file())
            standard = (root / "run" / "standardise.tsv").read_text().splitlines()
            boundaries = (root / "run" / "aux-boundary.tsv").read_text().splitlines()
            detections = (root / "run" / "detect.tsv").read_text().splitlines()
            self.assertEqual(len(standard), 27)
            self.assertIn("199912", standard[0])
            self.assertTrue(any("200101" in row for row in standard))
            self.assertIn("2100-01-01T00:00:00", boundaries[0])
            self.assertEqual(len(detections), 24)


if __name__ == "__main__":
    unittest.main()
