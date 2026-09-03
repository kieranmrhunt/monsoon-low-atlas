from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cmip6_pipeline.qa import SCHEMA, validate_catalogue


def catalogue() -> pd.DataFrame:
    hours = 72
    return pd.DataFrame(
        {
            "track_id": np.repeat(7, hours),
            "row_id": np.arange(hours),
            "time": pd.date_range("2000-07-01", periods=hours, freq="h"),
            "lon": np.linspace(80.0, 82.0, hours),
            "lat": np.linspace(20.0, 21.0, hours),
            "position_source": np.repeat("observed", hours),
            "max_vort_smoothed": np.repeat(8.0, hours),
            "precip_24hr": np.repeat(20.0, hours),
            "pressure_deficit_hpa": np.repeat(5.0, hours),
            "p95_anomaly_wind_125km_ms": np.repeat(12.0, hours),
            "physics_complete": np.repeat(True, hours),
            "physics_gap_supported": np.repeat(True, hours),
            "v55_event_existence_gate": np.repeat("calibrated_physical_support_v1", hours),
            "event_peak_imd_category": np.repeat(2, hours),
            "event_peak_imd_label": np.repeat("depression", hours),
            "intensity_method": np.repeat("test", hours),
        }
    )


class CatalogueQaTest(unittest.TestCase):
    def write_inputs(self, root: Path, frame: pd.DataFrame) -> tuple[Path, Path]:
        source = root / "catalogue.parquet"
        frame.to_parquet(source, index=False)
        plan = root / "period-plan.json"
        plan.write_text(
            json.dumps(
                {
                    "core_start": "200007",
                    "core_end": "200009",
                    "run": {"source_id": "TestModel", "experiment_id": "ssp245"},
                }
            )
        )
        return source, plan

    def test_valid_catalogue_passes_structural_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, plan = self.write_inputs(Path(temporary), catalogue())
            result = validate_catalogue(source, plan, reference=None)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checks"]["non_hourly_steps"], 0)
        self.assertEqual(result["diagnostics"]["events"], 1)

    def test_duplicate_track_hour_fails(self) -> None:
        frame = catalogue()
        frame.loc[1, "time"] = frame.loc[0, "time"]
        with tempfile.TemporaryDirectory() as temporary:
            source, plan = self.write_inputs(Path(temporary), frame)
            result = validate_catalogue(source, plan, reference=None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["checks"]["duplicate_track_times"], 1)


if __name__ == "__main__":
    unittest.main()
