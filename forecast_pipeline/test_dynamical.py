"""Source-contract tests; no network access or Python 3.12 dependency needed."""
import unittest
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import xarray as xr

from forecast_pipeline.dynamical_reader import cumulative_rain, read_member, regional_field, unit_factor, transient_read
from forecast_pipeline.dynamical import AifsHybridAdapter, DynamicalAdapter, load_arrays
from forecast_pipeline.plan_dynamical import planned_cycles
from forecast_pipeline.forecast_core import GRID_LATS, GRID_LONS
from forecast_pipeline.sources import adapter_for, available_forecast_steps
from forecast_pipeline.plan_recent import planned_recent_cycles
from forecast_pipeline.versions import model_version


def synthetic_groups(model="aifs"):
    coords = {"init_time": [np.datetime64("2025-07-12T00")],
              "lead_time": np.array([0, 6, 12], dtype="timedelta64[h]"),
              "latitude": [21., 20.], "longitude": [80., 81.]}
    if model == "gefs-extended":
        coords["ensemble_member"] = [0, 1]
    dims = list(coords)
    shape = tuple(len(coords[name]) for name in dims)
    base = np.ones(shape, dtype=np.float32)
    rain = "total_precipitation_surface" if model == "gefs-extended" else "total_precipitation_run_total_surface"
    surface = xr.Dataset({
        "pressure_reduced_to_mean_sea_level": (dims, base * 100000, {"units": "Pa"}),
        "wind_u_10m": (dims, base * 3, {"units": "m s-1"}),
        "wind_v_10m": (dims, base * 4, {"units": "m s-1"}),
        rain: (dims, base * 2, {"units": "kg m-2"}),
    }, coords=coords)
    pcoords = {**coords, "pressure_level": [850, 700, 500]}
    pressure = xr.Dataset({name: (list(pcoords), np.ones(shape + (3,), dtype=np.float32) * n, {"units": "m s-1"})
                           for name, n in (("wind_u", 5), ("wind_v", 6))}, coords=pcoords)
    return surface, pressure


class DynamicalTests(unittest.TestCase):
    def test_throttled_reads_retry_but_invalid_data_does_not(self):
        from unittest.mock import Mock
        read = Mock(side_effect=[RuntimeError("SlowDown: reduce your request rate"), np.array([5.])])
        with patch("forecast_pipeline.dynamical_reader.time.sleep") as sleep:
            np.testing.assert_array_equal(transient_read(read), [5.])
        self.assertEqual(read.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        read = Mock(side_effect=ValueError("missing precipitation"))
        with self.assertRaisesRegex(ValueError, "missing precipitation"):
            transient_read(read)
        self.assertEqual(read.call_count, 1)

    def test_planner_excludes_pre_rainfall_aifs_and_reuses_complete_cycles(self):
        inventory = {"aifs": {"cycles": ["2024-07-12T00:00:00", "2025-02-24T00:00:00", "2025-02-24T06:00:00"], "steps": list(range(0, 361, 6))}}
        start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC)
        existing = {"archive": [{"model": "aifs", "cycle": "2025022406", "cycle_utc": "2025-02-24T06:00:00Z",
                                  "valid_end_utc": "2025-03-11T06:00:00Z", "members_available": 1, "weather_fields": ["precipitation", "vorticity"]}]}
        rows, pending = planned_cycles(inventory, start, end, existing)
        self.assertEqual([row["cycle"] for row in rows], ["2025022406"])
        self.assertEqual(pending, [])
        existing["archive"][0]["weather_fields"] = []
        self.assertEqual(len(planned_cycles(inventory, start, end, existing)[1]), 1)

    def test_reader_rejects_known_missing_precipitation_before_network_access(self):
        with self.assertRaisesRegex(ValueError, "complete frozen-tracker inputs"):
            read_member("aifs", "2024040100", "det", [0, 6], np.array([20.]), np.array([80.]))

    def test_cache_identity_dimensions_and_missing_data_are_checked(self):
        metadata = {"schema": "mla-dynamical-member-v1", "dataset": "ecmwf-aifs-single-forecast-virtual",
                    "model": "aifs", "cycle": "2025071200", "member": "det", "steps": [0, 6]}
        arrays = {key: np.ones((2, len(GRID_LATS), len(GRID_LONS)), dtype=np.float32)
                  for key in ["mslp", "u10", "v10", "rain", "u850", "v850", "u700", "v700", "u500", "v500"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "member.npz"
            def write():
                np.savez(path, **arrays, latitude=GRID_LATS, longitude=GRID_LONS, metadata=np.asarray(json.dumps(metadata)))
            write()
            load_arrays(path, "aifs", "2025071200", "det", [0, 6])
            with self.assertRaisesRegex(ValueError, "identity"):
                load_arrays(path, "aifs", "2025071206", "det", [0, 6])
            with self.assertRaisesRegex(ValueError, "identity"):
                load_arrays(path, "aifs", "2025071200", "det", [0, 6, 12])
            arrays["u850"][0, 0, 0] = np.nan
            write()
            with self.assertRaisesRegex(ValueError, "missing"):
                load_arrays(path, "aifs", "2025071200", "det", [0, 6])

    def test_rain_accumulations_are_not_confused(self):
        interval = np.array([np.nan, 2, 3], dtype=np.float32)[:, None, None]
        np.testing.assert_array_equal(cumulative_rain(interval, [0, 6, 12], "gefs-extended").ravel(), [0, 2, 5])
        np.testing.assert_array_equal(cumulative_rain(interval, [0, 6, 12], "aifs").ravel(), [0, 2, 3])
        with self.assertRaises(ValueError):
            cumulative_rain(np.array([0, 3, 1])[:, None, None], [0, 6, 12], "aifs")
        with self.assertRaises(ValueError):
            cumulative_rain(interval, [0, 6, 18], "gefs-extended")
        with self.assertRaises(ValueError):
            cumulative_rain(np.array([0, np.nan, 1])[:, None, None], [0, 6, 12], "gefs-extended")

    def test_units_are_explicit_not_inferred_from_magnitude(self):
        self.assertEqual(unit_factor("Pa", "pressure"), .01)
        self.assertEqual(unit_factor("kg m-2", "rain"), 1)
        self.assertEqual(unit_factor("m", "rain"), 1000)
        with self.assertRaises(ValueError):
            unit_factor("kg m-2 s-1", "rain")

    def test_reads_correct_cycle_member_grid_and_levels(self):
        for model, member in (("aifs", "det"), ("gefs-extended", "p01")):
            with self.subTest(model=model):
                arrays, metadata = read_member(model, "2025071200", member, [0, 6, 12],
                    np.array([20., 21.]), np.array([80., 81.]), groups=synthetic_groups(model))
                self.assertEqual(arrays["mslp"].shape, (3, 2, 2))
                np.testing.assert_allclose(arrays["mslp"], 1000)
                np.testing.assert_allclose(arrays["rain"][-1], 4 if model == "gefs-extended" else 2)
                self.assertEqual(metadata["member"], member)
                self.assertEqual(metadata["finite_fraction"]["u850"], 1)

    def test_missing_cycle_or_level_is_not_nearest_filled(self):
        with self.assertRaises(KeyError):
            read_member("aifs", "2025071206", "det", [0, 6, 12],
                        np.array([20., 21.]), np.array([80., 81.]), groups=synthetic_groups())
        surface, pressure = synthetic_groups()
        with self.assertRaises(KeyError):
            read_member("aifs", "2025071200", "det", [0, 6, 12],
                        np.array([20., 21.]), np.array([80., 81.]),
                        groups=(surface, pressure.sel(pressure_level=[850, 500])))

    def test_misaligned_coordinates_rejected(self):
        surface, _ = synthetic_groups()
        with self.assertRaises(ValueError):
            regional_field(surface.wind_u_10m, {"init_time": np.datetime64("2025-07-12"),
                "lead_time": np.timedelta64(6, "h")}, np.array([22.]), np.array([80.]))

    def test_extended_schedule_and_leads(self):
        cycle = datetime(2026, 10, 5, tzinfo=UTC)
        self.assertEqual(available_forecast_steps("gefs-extended", cycle), list(range(0, 841, 6)))
        with self.assertRaises(ValueError):
            available_forecast_steps("gefs-extended", cycle.replace(hour=6))
        rows = planned_recent_cycles({"latest": {"gefs-extended": {"cycle_utc": "2026-10-05T00:00:00Z"}}}, 72)
        self.assertEqual([row["cycle"] for row in rows], ["2026100500", "2026100400", "2026100300", "2026100200"])
        self.assertIsInstance(adapter_for("gefs-extended"), DynamicalAdapter)
        self.assertIsInstance(adapter_for("aifs"), AifsHybridAdapter)

    def test_aifs_preserves_live_route_and_labels_experimental_history(self):
        adapter = AifsHybridAdapter()
        with patch("forecast_pipeline.dynamical.utc_now", return_value=datetime(2026, 10, 5, tzinfo=UTC)):
            self.assertTrue(adapter.historical("2025071200"))
            self.assertFalse(adapter.historical("latest"))
            self.assertFalse(adapter.historical("2026100400"))
        self.assertEqual(model_version("aifs", datetime(2024, 7, 1, tzinfo=UTC))["label"], "AIFS experimental")
        self.assertEqual(model_version("aifs", datetime(2025, 7, 12, tzinfo=UTC))["label"], "AIFS Single v1")
        self.assertEqual(model_version("gefs-extended", datetime(2025, 7, 12, tzinfo=UTC))["label"], "GEFS v12 family")

    def test_latest_inventory_does_not_select_future_or_stale_cycles(self):
        adapter = DynamicalAdapter("gefs-extended")
        adapter._inventory = {"cycles": ["2026-10-01T00:00:00", "2026-10-05T00:00:00", "2026-10-06T00:00:00"],
                              "steps": list(range(0, 841, 6)), "pressure_levels": [850, 700, 500]}
        with patch("forecast_pipeline.dynamical.utc_now", return_value=datetime(2026, 10, 5, 10, tzinfo=UTC)):
            cycle, steps = adapter.resolve_available_cycle("latest")
        self.assertEqual(cycle.strftime("%Y%m%d%H"), "2026100500")
        self.assertEqual(steps[-1], 840)


if __name__ == "__main__":
    unittest.main()
