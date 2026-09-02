from __future__ import annotations

import unittest
from pathlib import Path

from .track import source_paths


class ReanalysisTrackPathTest(unittest.TestCase):
    def test_tracker_inputs_are_absolute_when_tracker_runs_elsewhere(self) -> None:
        paths = source_paths(Path("data/reanalyses/merra2"))
        self.assertTrue(all(path.is_absolute() for path in paths.values()))
        self.assertTrue(str(paths["vorticity"]).endswith("/standard/vorticity"))


if __name__ == "__main__":
    unittest.main()
