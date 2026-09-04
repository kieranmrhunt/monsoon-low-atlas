from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from reanalysis_pipeline.common import sha256

from .warming import SCHEMA, attach_to_climate_bundle, build_registry, spherical_cell_weights


class WarmingTests(unittest.TestCase):
    def test_spherical_weights_use_latitude_bounds(self):
        dataset = xr.Dataset(
            coords={
                "lat": ("lat", [-60.0, 0.0, 60.0], {"bounds": "lat_bnds"}),
                "lon": ("lon", [90.0, 270.0], {"bounds": "lon_bnds"}),
            },
            data_vars={
                "lat_bnds": (("lat", "bnds"), [[-90.0, -30.0], [-30.0, 30.0], [30.0, 90.0]]),
                "lon_bnds": (("lon", "bnds"), [[0.0, 180.0], [180.0, 360.0]]),
            },
        )
        weights = spherical_cell_weights(dataset).values
        self.assertAlmostEqual(float(weights.sum()), 4.0 * np.pi)
        self.assertAlmostEqual(float(weights[1, 0] / weights[0, 0]), 2.0)

    @staticmethod
    def _write_period(
        badc_root: Path,
        run_root: Path,
        experiment: str,
        value: float,
    ) -> dict:
        activity = "CMIP" if experiment == "historical" else "ScenarioMIP"
        source_label = f"TestModel_{experiment}_r1i1p1f1_gn"
        field_root = (
            badc_root
            / activity
            / "TEST"
            / "TestModel"
            / experiment
            / "r1i1p1f1"
            / "Amon"
            / "tas"
            / "gn"
            / "v1"
        )
        field_root.mkdir(parents=True)
        starts = pd.date_range("2000-01-01", periods=12, freq="MS")
        ends = pd.date_range("2000-02-01", periods=12, freq="MS")
        times = starts + pd.Timedelta(days=15)
        dataset = xr.Dataset(
            {
                "tas": (("time", "lat", "lon"), np.full((12, 2, 2), value, dtype=np.float32), {"units": "K"}),
                "time_bnds": (("time", "bnds"), np.column_stack((starts.values, ends.values))),
                "lat_bnds": (("lat", "bnds"), [[-90.0, 0.0], [0.0, 90.0]]),
                "lon_bnds": (("lon", "bnds"), [[0.0, 180.0], [180.0, 360.0]]),
            },
            coords={
                "time": ("time", times, {"bounds": "time_bnds"}),
                "lat": ("lat", [-45.0, 45.0], {"bounds": "lat_bnds"}),
                "lon": ("lon", [90.0, 270.0], {"bounds": "lon_bnds"}),
            },
        )
        filename = f"tas_Amon_TestModel_{experiment}_r1i1p1f1_gn_200001-200012.nc"
        dataset.to_netcdf(field_root / filename)
        period_root = run_root / source_label
        period_root.mkdir(parents=True)
        period = {
            "run": {
                "activity": activity,
                "institution": "TEST",
                "source_id": "TestModel",
                "experiment_id": experiment,
                "member_id": "r1i1p1f1",
                "grid_label": "gn",
            },
            "source_label": source_label,
            "core_start": "200001",
            "core_end": "200012",
        }
        (period_root / "period-plan.json").write_text(json.dumps(period), encoding="utf-8")
        return period

    def test_build_and_attach_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            badc = root / "badc"
            run_root = root / "run"
            historical = self._write_period(badc, run_root, "historical", 280.0)
            future = self._write_period(badc, run_root, "ssp245", 282.5)
            (run_root / "plan.json").write_text(
                json.dumps({"periods": [historical, future]}), encoding="utf-8"
            )
            warming_manifest = build_registry([run_root], root / "warming", badc)
            warming_meta = json.loads(warming_manifest.read_text(encoding="utf-8"))
            warming_asset = warming_manifest.parent / warming_meta["asset"]["path"]
            with gzip.open(warming_asset, "rt", encoding="utf-8") as stream:
                warming = json.load(stream)
            self.assertEqual(warming["schema"], SCHEMA)
            self.assertAlmostEqual(warming["pairs"][0]["change_k"], 2.5)
            self.assertEqual(warming["pairs"][0]["historical"]["period"]["months"], 12)

            pair_id = warming["pairs"][0]["id"]
            climate_root = root / "climate"
            climate_root.mkdir()
            index = {
                "schema": "lps-atlas-cmip6-climate-index-v1",
                "generated_utc": "test",
                "status": "multi-model-awaiting-review",
                "pairs": [
                    {"id": pair_id, "source_label": "TestModel", "member_id": "r1i1p1f1"},
                    {"id": "ensemble", "kind": "multi-model", "model_ids": [pair_id]},
                ],
            }
            index_path = climate_root / "index.json.gz"
            with gzip.open(index_path, "wt", encoding="utf-8") as stream:
                json.dump(index, stream)
            climate_manifest = climate_root / "manifest.json"
            climate_manifest.write_text(
                json.dumps(
                    {
                        "schema": index["schema"],
                        "index": {"path": index_path.name, "sha256": sha256(index_path)},
                    }
                ),
                encoding="utf-8",
            )
            attached = attach_to_climate_bundle(climate_manifest, warming_manifest)
            with gzip.open(attached, "rt", encoding="utf-8") as stream:
                updated = json.load(stream)
            self.assertAlmostEqual(updated["pairs"][0]["warming"]["change_k"], 2.5)
            self.assertAlmostEqual(updated["pairs"][1]["warming"]["mean_change_k"], 2.5)


if __name__ == "__main__":
    unittest.main()
