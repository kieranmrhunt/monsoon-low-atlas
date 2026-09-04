import unittest

import numpy as np
import pandas as pd

from .impact import (
    ENSEMBLE_SCHEMA,
    INDIA_METRICS,
    REGIONAL_RAIN_METRICS,
    SEASONS,
    _monthly_control_excess,
    aggregate_impact_payloads,
    native_components,
)
from .model_calendar import time_axis


class ImpactTests(unittest.TestCase):
    def test_native_components_preserve_360_day_months(self):
        axis = time_axis("360_day", "198101")
        analysis = pd.date_range("1981-01-01", periods=60 * 24, freq="h")
        result = native_components(analysis, axis)
        self.assertEqual(tuple(result.iloc[-1][["month", "day", "hour"]]), (2, 30, 23))

    def test_month_control_excess_uses_only_unexposed_days(self):
        rain = np.asarray([[10.0, 4.0], [2.0, 4.0], [2.0, 4.0]])
        exposed = np.asarray([[True, False], [False, False], [False, False]])
        months = np.asarray([6, 6, 6])
        value = _monthly_control_excess(rain, exposed, months, np.ones(2))
        self.assertAlmostEqual(value, 8.0)

    def test_impact_ensemble_gives_each_model_one_vote(self):
        def entry(model_id, historical, future):
            changes = {
                metric: {
                    "historical": historical,
                    "future": future,
                    "absolute_change": future - historical,
                    "percent_change": (future / historical - 1.0) * 100.0,
                    "ci05": future - historical - 0.1,
                    "ci95": future - historical + 0.1,
                    "percent_ci05": (future / historical - 1.0) * 100.0 - 1.0,
                    "percent_ci95": (future / historical - 1.0) * 100.0 + 1.0,
                }
                for metric in INDIA_METRICS
            }
            regional_changes = {
                metric: {
                    "historical": historical,
                    "future": future,
                    "absolute_change": future - historical,
                    "percent_change": (future / historical - 1.0) * 100.0,
                    "ci05": future - historical - 0.1,
                    "ci95": future - historical + 0.1,
                    "percent_ci05": (future / historical - 1.0) * 100.0 - 1.0,
                    "percent_ci95": (future / historical - 1.0) * 100.0 + 1.0,
                }
                for metric in REGIONAL_RAIN_METRICS
            }
            footprints = {
                season: {
                    "historical_samples": 2,
                    "future_samples": 2,
                    "historical_mean_mm": [[historical, historical], [historical, historical]],
                    "future_mean_mm": [[future, future], [future, future]],
                }
                for season in SEASONS
            }
            years = [{metric: historical for metric in INDIA_METRICS} for _ in range(2)]
            future_years = [{metric: future for metric in INDIA_METRICS} for _ in range(2)]
            region_years = [
                {metric: historical for metric in REGIONAL_RAIN_METRICS} for _ in range(2)
            ]
            future_region_years = [
                {metric: future for metric in REGIONAL_RAIN_METRICS} for _ in range(2)
            ]
            return {
                "id": model_id,
                "source_label": model_id,
                "pair": {
                    "india_jjas_changes": changes,
                    "regional_india_jjas_changes": {
                        "east": {
                            "label": "East",
                            "state_ids": ["odisha"],
                            "grid_cells": 2,
                            "changes": regional_changes,
                        }
                    },
                    "storm_centred_precipitation": {
                        "relative_longitude_deg": [-0.5, 0.5],
                        "relative_latitude_deg": [-0.5, 0.5],
                        "seasons": footprints,
                    },
                },
                "historical": {"india_jjas_rainfall": {"years": years, "regions": {
                    "east": {"years": region_years}
                }}},
                "future": {"india_jjas_rainfall": {"years": future_years, "regions": {
                    "east": {"years": future_region_years}
                }}},
            }

        result = aggregate_impact_payloads(
            [entry("Small", 1.0, 2.0), entry("Large", 9.0, 10.0)],
            samples=40,
        )
        rainfall = result["india_jjas_changes"]["rainfall_share"]
        footprint = result["storm_centred_precipitation"]["seasons"]["jjas"]
        self.assertEqual(result["schema"], ENSEMBLE_SCHEMA)
        self.assertEqual(result["model_count"], 2)
        self.assertAlmostEqual(rainfall["historical"], 5.0)
        self.assertAlmostEqual(rainfall["future"], 6.0)
        self.assertAlmostEqual(rainfall["percent_change"], 20.0)
        self.assertEqual(len(rainfall["models"]), 2)
        regional = result["regional_india_jjas_changes"]["east"]
        self.assertAlmostEqual(
            regional["changes"]["regional_mean_mm_day"]["percent_change"], 20.0
        )
        self.assertEqual(
            len(regional["changes"]["regional_mean_mm_day"]["models"]), 2
        )
        self.assertEqual(footprint["model_count"], 2)
        self.assertAlmostEqual(footprint["historical_mean_mm"][0][0], 5.0)
        self.assertAlmostEqual(footprint["future_mean_mm"][0][0], 6.0)


if __name__ == "__main__":
    unittest.main()
