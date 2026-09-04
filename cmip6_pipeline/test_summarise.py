from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cmip6_pipeline.model_calendar import time_axis
from cmip6_pipeline.summarise import (
    ENSEMBLE_SCHEMA,
    INDEX_SCHEMA,
    SCHEMA,
    annual_summary,
    aggregate_change_payloads,
    assemble_ensemble,
    bootstrap_change,
    event_summary,
    publish_pair,
    summarise_pair,
    summarise_run,
)
from reanalysis_pipeline.common import sha256


class SummariseTest(unittest.TestCase):
    def test_event_season_uses_native_model_month_not_analysis_clock(self) -> None:
        frame = pd.DataFrame(
            {
                "track_id": [4, 4],
                "time": pd.to_datetime(["1981-03-01T00:00", "1981-03-01T01:00"]),
                "lon": [80.0, 80.1],
                "lat": [20.0, 20.1],
                "event_peak_imd_category": [1, 1],
                "p95_anomaly_wind_125km_ms": [9.0, 9.0],
                "pressure_deficit_hpa": [3.0, 3.0],
                "max_vort_smoothed": [7.0, 7.0],
                "precip_24hr": [10.0, 10.0],
            }
        )
        annotated = time_axis("360_day", "198101").annotate(frame)
        result = event_summary(annotated).iloc[0]
        self.assertEqual(result.genesis_month, 2)
        self.assertEqual(result.model_start, "1981-02-30T00:00:00")

    def test_multimodel_percent_change_matches_displayed_ensemble_means(self) -> None:
        def payload(historical: float, future: float, role: str) -> dict:
            value = historical if role == "historical" else future
            annual = [{metric: value for metric in (
                "systems",
                "depressions_or_stronger",
                "deep_depressions_or_stronger",
                "cyclonic_storms_or_stronger",
                "system_days",
                "mean_duration_hours",
                "mean_peak_wind_ms",
                "mean_peak_pressure_deficit_hpa",
                "mean_peak_24h_precipitation_mm",
            )}]
            return {
                "coverage": {"years": 1},
                "seasonal": {
                    season: {
                        "annual": annual,
                        "track_density": {
                            "latitude_edges": [0.0, 1.0],
                            "longitude_edges": [70.0, 71.0],
                            "unique_track_counts": [[value]],
                        },
                    }
                    for season in ("all", "jjas", "mam", "ond", "djf")
                }
            }

        historical = [payload(1.0, 2.0, "historical"), payload(9.0, 10.0, "historical")]
        future = [payload(1.0, 2.0, "future"), payload(9.0, 10.0, "future")]
        result = aggregate_change_payloads(
            historical,
            future,
            model_ids=["small-baseline", "large-baseline"],
            samples=20,
        )
        systems = result["seasonal_changes"]["jjas"]["systems"]
        self.assertEqual(result["schema"], ENSEMBLE_SCHEMA)
        self.assertAlmostEqual(systems["historical"], 5.0)
        self.assertAlmostEqual(systems["future"], 6.0)
        self.assertAlmostEqual(systems["absolute_change"], 1.0)
        self.assertAlmostEqual(systems["percent_change"], 20.0)
        self.assertAlmostEqual(systems["mean_model_percent_change"], 55.5555555556)
        agreement = result["track_density_agreement"]["jjas"]
        self.assertEqual(agreement["positive_models"], [[2]])
        self.assertEqual(agreement["negative_models"], [[0]])
        self.assertEqual(agreement["signed_agreement_fraction"], [[1.0]])
        self.assertTrue(all(value["ci05"] <= value["absolute_change"] <= value["ci95"] for value in systems["models"]))
        self.assertTrue(all(value["percent_ci05"] <= value["percent_change"] <= value["percent_ci95"] for value in systems["models"]))

    def test_event_summary_uses_complete_track_geometry(self) -> None:
        frame = pd.DataFrame(
            {
                "track_id": [4, 4],
                "time": pd.date_range("2000-07-01", periods=2, freq="h"),
                "lon": [80.0, 81.0],
                "lat": [20.0, 20.0],
                "event_peak_imd_category": [2, 2],
                "p95_anomaly_wind_125km_ms": [11.0, 12.0],
                "pressure_deficit_hpa": [4.0, 5.0],
                "max_vort_smoothed": [8.0, 9.0],
                "precip_24hr": [20.0, 24.0],
            }
        )
        result = event_summary(frame).iloc[0]
        self.assertEqual(result.duration_hours, 2)
        self.assertEqual(result.peak_category, 2)
        self.assertGreater(result.path_length_km, 100.0)

    def test_bootstrap_change_is_deterministic(self) -> None:
        historical = np.asarray([1.0, 2.0, 3.0])
        future = np.asarray([2.0, 3.0, 4.0])
        first = bootstrap_change(historical, future, seed=4, samples=100)
        second = bootstrap_change(historical, future, seed=4, samples=100)
        self.assertEqual(first, second)
        self.assertEqual(first["absolute_change"], 1.0)

    def test_empty_season_retains_complete_year_axis(self) -> None:
        events = pd.DataFrame(
            {
                "genesis_year": [2000],
                "genesis_month": [7],
                "peak_category": [1],
                "duration_hours": [24],
                "peak_wind_ms": [8.0],
                "peak_pressure_deficit_hpa": [3.0],
                "peak_24h_precipitation_mm": [12.0],
            }
        )
        result = annual_summary(events, 2000, 2001, (3, 4, 5))
        self.assertEqual(result.year.tolist(), [2000, 2001])
        self.assertEqual(result.systems.tolist(), [0, 0])
        self.assertTrue(result.mean_duration_hours.isna().all())

    def test_public_bundle_is_relocatable_and_seasonal(self) -> None:
        def catalogue(year: int, track_id: int) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "track_id": [track_id, track_id],
                    "time": pd.date_range(f"{year}-07-01", periods=2, freq="h"),
                    "lon": [80.0, 81.0],
                    "lat": [20.0, 20.0],
                    "event_peak_imd_category": [2, 2],
                    "p95_anomaly_wind_125km_ms": [11.0, 12.0],
                    "pressure_deficit_hpa": [4.0, 5.0],
                    "max_vort_smoothed": [8.0, 9.0],
                    "precip_24hr": [20.0, 24.0],
                    "intensity_method": ["test", "test"],
                }
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifests = []
            for role, year, experiment, track_id in (
                ("historical", 2000, "historical", 1),
                ("future", 2080, "ssp245", 2),
            ):
                source = root / f"{role}.parquet"
                catalogue(year, track_id).to_parquet(source, index=False)
                qa_report = root / f"{role}-qa.json"
                qa_report.write_text(
                    json.dumps(
                        {
                            "schema": "lps-atlas-cmip6-catalogue-qa-v1",
                            "status": "passed",
                            "catalogue": {"sha256": sha256(source)},
                            "checks": {"duplicate_track_times": 0},
                        }
                    )
                )
                output = root / role
                summarise_run(
                    source,
                    output,
                    source_label="TestModel",
                    experiment_id=experiment,
                    member_id="r1i1p1f1",
                    period_label=str(year),
                    qa_report=qa_report,
                )
                manifest = output / "manifest.json"
                manifests.append(manifest)
                metadata = json.loads(manifest.read_text())
                self.assertEqual(metadata["schema"], SCHEMA)
                with gzip.open(output / metadata["asset"]["path"], "rt", encoding="utf-8") as stream:
                    payload = json.load(stream)
                self.assertNotIn(str(root), json.dumps(payload["provenance"]))
                self.assertIn("jjas", payload["seasonal"])
                self.assertEqual(payload["qa"]["status"], "passed")
            paired = root / "paired"
            summarise_pair(manifests[0], manifests[1], paired)
            public = root / "public"
            index_path = publish_pair(manifests[0], manifests[1], paired / "manifest.json", public)
            self.assertTrue(index_path.is_file())
            index_manifest = json.loads((public / "manifest.json").read_text())
            self.assertEqual(index_manifest["schema"], INDEX_SCHEMA)
            self.assertEqual(index_manifest["pairs"], 1)
            with gzip.open(index_path, "rt", encoding="utf-8") as stream:
                index = json.load(stream)
            self.assertEqual(index["status"], "engineering-canary")

    def test_ensemble_index_uses_one_vote_per_source_model(self) -> None:
        def catalogue(year: int, track_id: int, wind: float) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "track_id": [track_id, track_id],
                    "time": pd.date_range(f"{year}-07-01", periods=2, freq="h"),
                    "lon": [80.0, 81.0],
                    "lat": [20.0, 20.0],
                    "event_peak_imd_category": [2, 2],
                    "p95_anomaly_wind_125km_ms": [wind, wind + 1.0],
                    "pressure_deficit_hpa": [4.0, 5.0],
                    "max_vort_smoothed": [8.0, 9.0],
                    "precip_24hr": [20.0, 24.0],
                    "intensity_method": ["test", "test"],
                }
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public_manifests = []
            for model_index, model in enumerate(("ModelA", "ModelB"), start=1):
                run_manifests = []
                for role, year, experiment, start_year, end_year in (
                    ("historical", 2000, "historical", 1981, 2010),
                    ("future", 2080, "ssp245", 2071, 2100),
                ):
                    source = root / f"{model}-{role}.parquet"
                    catalogue(year, model_index, 9.0 + model_index + (role == "future")).to_parquet(
                        source, index=False
                    )
                    qa_report = root / f"{model}-{role}-qa.json"
                    qa_report.write_text(
                        json.dumps(
                            {
                                "schema": "lps-atlas-cmip6-catalogue-qa-v1",
                                "status": "passed",
                                "catalogue": {"sha256": sha256(source)},
                                "checks": {"duplicate_track_times": 0},
                                "historical_screen": (
                                    {
                                        "screening_status": "passes-basic-historical-screen",
                                        "diagnostic_flags": [],
                                        "comparisons": {"event_frequency_ratio": 0.9},
                                        "classification_screen": {
                                            "screening_status": "review-classification-bias",
                                            "comparisons": {
                                                "depression_or_stronger_frequency_ratio": 0.3
                                            },
                                        },
                                        "seasonal": {
                                            "jjas": {
                                                "screening_status": "review-model-bias",
                                                "diagnostic_flags": ["low_event_frequency"],
                                                "comparisons": {"event_frequency_ratio": 0.5},
                                            }
                                        },
                                    }
                                    if role == "historical" else None
                                ),
                            }
                        )
                    )
                    output = root / model / role
                    summarise_run(
                        source,
                        output,
                        source_label=model,
                        experiment_id=experiment,
                        member_id="r1i1p1f1",
                        period_label=f"{start_year}–{end_year}",
                        start_year=start_year,
                        end_year=end_year,
                        qa_report=qa_report,
                    )
                    run_manifests.append(output / "manifest.json")
                paired = root / model / "paired"
                summarise_pair(run_manifests[0], run_manifests[1], paired)
                public = root / model / "public"
                publish_pair(run_manifests[0], run_manifests[1], paired / "manifest.json", public)
                public_manifests.append(public / "manifest.json")

            combined = root / "combined"
            index_path = assemble_ensemble(public_manifests, combined)
            with gzip.open(index_path, "rt", encoding="utf-8") as stream:
                index = json.load(stream)
            self.assertEqual(index["status"], "multi-model-awaiting-review")
            self.assertEqual(index["ensemble"]["model_count"], 2)
            screen = index["ensemble"]["historical_screening"][0]
            self.assertEqual(screen["comparisons"]["event_frequency_ratio"], 0.9)
            self.assertEqual(
                screen["classification"]["screening_status"],
                "review-classification-bias",
            )
            self.assertEqual(screen["jjas"]["status"], "review-model-bias")
            self.assertEqual(
                screen["jjas"]["comparisons"]["event_frequency_ratio"], 0.5
            )
            self.assertIn("model", screen)
            self.assertIn("reference_metrics", screen)
            ensemble = next(pair for pair in index["pairs"] if pair.get("kind") == "multi-model")
            self.assertEqual(ensemble["source_label"], "Multi-model mean")
            self.assertEqual(ensemble["comparison"]["baseline"], "1981–2010")
            self.assertEqual(ensemble["comparison"]["future"], "2071–2100")
            with gzip.open(combined / ensemble["historical"]["url"], "rt", encoding="utf-8") as stream:
                historical_ensemble = json.load(stream)
            self.assertIn("genesis_density", historical_ensemble["seasonal"]["jjas"])
            self.assertIn("lysis_density", historical_ensemble["seasonal"]["jjas"])
            self.assertGreaterEqual(len(historical_ensemble["metric_definitions"]), 59)
            with gzip.open(combined / ensemble["change"]["url"], "rt", encoding="utf-8") as stream:
                change = json.load(stream)
            self.assertEqual(change["schema"], ENSEMBLE_SCHEMA)
            self.assertEqual(change["seasonal_changes"]["jjas"]["systems"]["model_count"], 2)
            self.assertAlmostEqual(
                change["seasonal_changes"]["jjas"]["mean_peak_wind_ms"]["absolute_change"],
                1.0,
            )
            with self.assertRaisesRegex(ValueError, "review file"):
                assemble_ensemble(
                    public_manifests,
                    root / "unreviewed",
                    status="validated-production-window",
                )
            review = root / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "schema": "lps-atlas-cmip6-ensemble-review-v1",
                        "status": "approved",
                        "included_pair_ids": index["ensemble"]["included_pair_ids"],
                        "approved_by": "Test Reviewer",
                        "approved_utc": "2026-01-01T00:00:00Z",
                    }
                )
            )
            approved_path = assemble_ensemble(
                public_manifests,
                root / "approved",
                status="validated-production-window",
                review_file=review,
            )
            with gzip.open(approved_path, "rt", encoding="utf-8") as stream:
                approved = json.load(stream)
            self.assertEqual(approved["status"], "validated-production-window")
            self.assertEqual(approved["review"]["approved_by"], "Test Reviewer")


if __name__ == "__main__":
    unittest.main()
