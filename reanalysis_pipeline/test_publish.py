from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from .match import MATCH_SCHEMA
from .publish import MANIFEST_SCHEMA, build_manifest, publish


class PublishReanalysisTest(unittest.TestCase):
    def asset(self, root: Path, source: str = "merra2") -> Path:
        path = root / f"{source}.json.gz"
        value = {
            "schema": MATCH_SCHEMA,
            "source": source,
            "coverage_start_utc": "2016-07-01T00:00:00Z",
            "coverage_end_utc": "2016-07-31T23:00:00Z",
            "matches": [{"era5_track_id": 10, "source_track_id": "m-1"}],
            "tracks": {"m-1": [[1, 80.0, 20.0, "o"], [2, 80.5, 20.1, "o"]]},
            "method": {},
            "qa": {"selected_matches": 1},
        }
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            json.dump(value, stream)
        return path

    def test_missing_source_is_explicitly_processing(self) -> None:
        value = build_manifest({})
        self.assertEqual(value["schema"], MANIFEST_SCHEMA)
        self.assertEqual(value["sources"]["imdaa"]["status"], "processing")
        self.assertEqual(value["sources"]["jra55"]["status"], "processing")
        self.assertEqual(value["sources"]["erainterim"]["status"], "processing")

    def test_publish_copies_validated_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = publish(root / "public", {"merra2": self.asset(root)})
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["sources"]["merra2"]["status"], "ready")
            self.assertTrue((manifest_path.parent / manifest["sources"]["merra2"]["matches_url"]).is_file())

    def test_later_publish_retains_existing_ready_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            publish(public, {"merra2": self.asset(root)})
            manifest_path = publish(public, {"jra55": self.asset(root, "jra55")})
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["sources"]["merra2"]["status"], "ready")
            self.assertEqual(manifest["sources"]["jra55"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
