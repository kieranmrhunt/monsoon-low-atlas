from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cmip6_pipeline.rebuild_browser import attach_comparison_bundle
from cmip6_pipeline.summarise import INDEX_SCHEMA, atomic_gzip_json, atomic_json
from reanalysis_pipeline.common import sha256


class RebuildBrowserTest(unittest.TestCase):
    def _bundle(self, root: Path, pairs: list[dict], *, ensemble: dict | None = None) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        index = {
            "schema": INDEX_SCHEMA,
            "generated_utc": "2026-01-01T00:00:00Z",
            "status": "multi-model-awaiting-review",
            "pairs": pairs,
            "defaults": {"pair": pairs[0]["id"] if pairs else "", "season": "jjas", "metric": "systems"},
        }
        if ensemble is not None:
            index["ensemble"] = ensemble
        raw = json.dumps(index, separators=(",", ":")).encode()
        path = root / f"climate-index.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz"
        atomic_gzip_json(path, index)
        manifest = root / "manifest.json"
        atomic_json(
            manifest,
            {
                "schema": INDEX_SCHEMA,
                "generated_utc": index["generated_utc"],
                "status": index["status"],
                "index": {"path": path.name, "sha256": sha256(path), "bytes": path.stat().st_size},
                "pairs": len(pairs),
                "models": 2,
            },
        )
        return manifest

    def test_attach_comparison_bundle_copies_assets_and_preserves_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_manifest = self._bundle(root / "target", [{"id": "existing"}])
            source_root = root / "source"
            assets = source_root / "assets"
            assets.mkdir(parents=True)
            references = {}
            for role in ("historical", "future", "change"):
                path = assets / f"{role}.json.gz"
                with gzip.open(path, "wt", encoding="utf-8") as stream:
                    json.dump({"role": role}, stream)
                references[role] = {
                    "url": f"assets/{path.name}",
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }
            pair = {
                "id": "highres-ensemble",
                "kind": "multi-model",
                "comparison_basis": "time-slice",
                "source_label": "Multi-model mean",
                **references,
            }
            source_manifest = self._bundle(
                source_root,
                [pair],
                ensemble={
                    "id": "highres-ensemble",
                    "model_count": 3,
                    "included_pair_ids": ["a", "b", "c"],
                },
            )
            output = attach_comparison_bundle(
                target_manifest,
                source_manifest,
                collection_id="highresmip",
                collection_label="HighResMIP mid-century",
            )
            with gzip.open(output, "rt", encoding="utf-8") as stream:
                index = json.load(stream)
            self.assertEqual(index["defaults"]["pair"], "existing")
            self.assertEqual({entry["id"] for entry in index["pairs"]}, {"existing", "highres-ensemble"})
            self.assertEqual(index["comparison_collections"][0]["model_count"], 3)
            for role in references:
                self.assertTrue((root / "target" / references[role]["url"]).is_file())
            manifest = json.loads(target_manifest.read_text())
            self.assertEqual(manifest["pairs"], 2)
            self.assertEqual(manifest["highresmip_models"], 3)

            repeated = attach_comparison_bundle(
                target_manifest,
                source_manifest,
                collection_id="highresmip",
                collection_label="HighResMIP mid-century",
            )
            with gzip.open(repeated, "rt", encoding="utf-8") as stream:
                repeated_index = json.load(stream)
            self.assertEqual([entry["id"] for entry in repeated_index["pairs"]], ["existing", "highres-ensemble"])


if __name__ == "__main__":
    unittest.main()
