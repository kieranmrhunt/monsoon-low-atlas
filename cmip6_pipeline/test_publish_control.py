from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from reanalysis_pipeline.common import sha256

from .era5_control import SOURCE_LABEL
from .publish_control import CONTROL_ID, attach_resolution_control
from .summarise import INDEX_SCHEMA, SCHEMA, atomic_gzip_json


class PublishControlTests(unittest.TestCase):
    def test_attach_preserves_bundle_and_adds_validated_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = {
                "schema": INDEX_SCHEMA,
                "generated_utc": "before",
                "status": "multi-model-awaiting-review",
                "pairs": [{"id": "pair", "impact": {"url": "assets/impact.json.gz"}}],
            }
            index_path = root / "climate-index.before.json.gz"
            atomic_gzip_json(index_path, index)
            climate_manifest = root / "manifest.json"
            climate_manifest.write_text(
                json.dumps(
                    {
                        "schema": INDEX_SCHEMA,
                        "generated_utc": "before",
                        "index": {
                            "path": index_path.name,
                            "sha256": sha256(index_path),
                            "bytes": index_path.stat().st_size,
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary_root = root / "summary"
            summary_root.mkdir()
            payload = {
                "schema": SCHEMA,
                "run": {"source_label": SOURCE_LABEL, "period_label": "1981–2010"},
                "coverage": {"start_year": 1981, "end_year": 2010, "years": 30},
                "qa": {"status": "passed", "historical_screen": {"comparisons": {"event_frequency_ratio": 0.9}}},
            }
            summary_asset = summary_root / "climate-run.control.json.gz"
            atomic_gzip_json(summary_asset, payload)
            summary_manifest = summary_root / "manifest.json"
            summary_manifest.write_text(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "asset": {
                            "path": summary_asset.name,
                            "sha256": sha256(summary_asset),
                            "bytes": summary_asset.stat().st_size,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = attach_resolution_control(climate_manifest, summary_manifest)
            updated_manifest = json.loads(climate_manifest.read_text(encoding="utf-8"))
            with gzip.open(result, "rt", encoding="utf-8") as stream:
                updated = json.load(stream)
            self.assertEqual(updated_manifest["resolution_controls"], 1)
            self.assertEqual(updated["pairs"], index["pairs"])
            self.assertEqual(updated["resolution_controls"][0]["id"], CONTROL_ID)
            copied = root / updated["resolution_controls"][0]["summary"]["url"]
            self.assertTrue(copied.is_file())
            self.assertEqual(sha256(copied), updated["resolution_controls"][0]["summary"]["sha256"])


if __name__ == "__main__":
    unittest.main()
