#!/usr/bin/env python3
"""Fast contract tests for the static forecast data pipeline."""

from __future__ import annotations

import base64
import gzip
import json
import stat
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from forecast_pipeline.forecast_core import (
    GRID_LATS,
    GRID_LONS,
    atomic_write_json,
    candidate_cycles,
    compact_weather,
    manifest_entry_horizon_hours,
    trailing_24h,
    validate_cycle_payload,
)
from forecast_pipeline.sources import (
    MODEL_DEFINITIONS,
    NcepAdapter,
    TIGGE_CENTRES,
    TIGGE_MODEL_IDS,
    TiggeAdapter,
    TiggeEcmwfAdapter,
    TiggeNcepAdapter,
    _fetch_record,
    adapter_for,
    available_forecast_steps,
    parse_ecmwf_index,
    parse_ncep_index,
)
from forecast_pipeline.tigge_catalogue import REQUIRED_FIELDS, TiggeAvailability
from forecast_pipeline.analysis_history import (
    analysis_centres,
    replace_analysis_entry,
)
from forecast_pipeline.archive import archive_manifest_entry, archive_payload
from forecast_pipeline.update import replace_recent_entry
from forecast_pipeline.v56_tracking import _hourly_axis, _longest_true_run, interpolate_hourly
from forecast_pipeline.versions import model_version
from forecast_pipeline.watch_archive_publish import target_state, update_progress


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, int | None] | None]] = []

    def get(self, url: str, *, byte_range: tuple[int, int | None] | None = None) -> bytes:
        self.calls.append((url, byte_range))
        return b"GRIB"


class StubVerifier:
    def verification(self, payload):
        return {"status": "no_match", "tracks": [], "matches": []}


class ForecastPipelineContractTests(unittest.TestCase):
    def test_candidate_cycles_are_six_hourly_and_descending(self) -> None:
        now = datetime(2026, 8, 30, 13, 47, tzinfo=UTC)
        values = candidate_cycles(now, limit=3)
        self.assertEqual([item.strftime("%Y%m%d%H") for item in values], ["2026083012", "2026083006", "2026083000"])

    def test_ncep_last_inventory_record_uses_open_ended_range(self) -> None:
        records = parse_ncep_index("1:0:d=20260830:PRMSL:mean sea level:\n2:120:d=20260830:VGRD:10 m above ground:\n")
        self.assertEqual(records[0].length, 120)
        self.assertEqual(records[1].length, -1)
        client = RecordingClient()
        self.assertEqual(_fetch_record(client, "https://example.test/file.grb2", records[1]), b"GRIB")
        self.assertEqual(client.calls, [("https://example.test/file.grb2", (120, None))])

    def test_ecmwf_json_index_preserves_member_metadata(self) -> None:
        line = '{"_offset":10,"_length":24,"param":"u","levelist":"850","number":"7"}'
        record = parse_ecmwf_index(line)[0]
        self.assertEqual((record.offset, record.length), (10, 24))
        self.assertEqual(record.attributes["number"], "7")

    def test_noaa_models_disclose_the_operational_cloud_feeds(self) -> None:
        for model in ("gfs", "gefs"):
            definition = MODEL_DEFINITIONS[model]
            self.assertEqual(definition.source_name, "NOAA Open Data cloud mirror")
            self.assertIn("registry.opendata.aws", definition.source_url)

    def test_available_step_schedules_retain_each_provider_horizon(self) -> None:
        cycle_00 = datetime(2026, 8, 30, 0, tzinfo=UTC)
        cycle_06 = datetime(2026, 8, 30, 6, tzinfo=UTC)
        self.assertEqual(available_forecast_steps("gfs", cycle_00)[-1], 384)
        self.assertEqual(available_forecast_steps("gefs", cycle_00)[-1], 384)
        self.assertEqual(available_forecast_steps("ifs", cycle_00)[-1], 360)
        self.assertEqual(available_forecast_steps("ifs", cycle_06)[-1], 144)
        self.assertEqual(available_forecast_steps("aifs-ens", cycle_06)[-1], 360)
        self.assertEqual(available_forecast_steps("tigge-ecmwf", cycle_00)[-1], 360)
        self.assertEqual(available_forecast_steps("tigge-jma", cycle_00)[-1], 264)
        self.assertEqual(available_forecast_steps("tigge-eccc", cycle_00)[-1], 384)
        self.assertEqual(available_forecast_steps("ukmo-global", cycle_00)[-1], 144)
        self.assertTrue(all(step % 6 == 0 for step in available_forecast_steps("gfs", cycle_00)))

    def test_tigge_queue_limit_errors_are_retryable_but_mars_errors_are_not(self) -> None:
        self.assertTrue(
            TiggeEcmwfAdapter._is_queue_limit_error(
                RuntimeError(
                    "The number of queued requests per user for ECDS datasets is limited"
                )
            )
        )
        self.assertTrue(
            TiggeEcmwfAdapter._is_queue_limit_error(
                RuntimeError("ERROR 101 USER_QUEUED_LIMIT_EXCEEDED")
            )
        )
        self.assertFalse(
            TiggeEcmwfAdapter._is_queue_limit_error(
                RuntimeError("mars - ERROR - requested fields are unavailable")
            )
        )

    def test_tigge_retrieval_waits_and_retries_a_full_queue(self) -> None:
        class QueueLimitedClient:
            calls = 0

            def __init__(self, **unused) -> None:
                pass

            def retrieve(self, unused_dataset, unused_request, target) -> None:
                type(self).calls += 1
                if type(self).calls < 3:
                    raise RuntimeError(
                        "The number of queued requests per user is limited"
                    )
                Path(target).write_bytes(b"GRIB")

        adapter = TiggeEcmwfAdapter()
        adapter.queue_retry_attempts = 3
        adapter.queue_retry_base_seconds = 1
        cycle = datetime(2016, 7, 1, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tigge.grib"
            with (
                patch.dict(
                    "sys.modules",
                    {"cdsapi": SimpleNamespace(Client=QueueLimitedClient)},
                ),
                patch.object(adapter, "_credentials", return_value="test-key"),
                patch("forecast_pipeline.sources.random.uniform", return_value=0),
                patch("forecast_pipeline.sources.time.sleep") as sleep,
            ):
                adapter._retrieve(cycle, [0], target, "cf", "pl")
            self.assertEqual(QueueLimitedClient.calls, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
            self.assertEqual(target.read_bytes(), b"GRIB")

    def test_tigge_adapter_uses_each_centres_archive_origin(self) -> None:
        requests = []

        class RecordingCdsClient:
            def __init__(self, **unused) -> None:
                pass

            def retrieve(self, unused_dataset, request, target) -> None:
                requests.append(request)
                Path(target).write_bytes(b"GRIB")

        adapter = TiggeAdapter("tigge-jma")
        cycle = datetime(2016, 7, 1, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict("sys.modules", {"cdsapi": SimpleNamespace(Client=RecordingCdsClient)}),
                patch.object(adapter, "_credentials", return_value="test-key"),
            ):
                adapter._retrieve(cycle, [0, 6], Path(directory) / "jma.grib", "pf", "pl")
        self.assertEqual(requests[0]["origin"], "rjtd")
        self.assertEqual(requests[0]["step"], "0/6")

    def test_tigge_catalogue_uses_complete_common_field_axis(self) -> None:
        def rows(origin: str, forecast_type: str, horizon: int) -> list[dict[str, object]]:
            output = []
            for variable, level_type, level_value in REQUIRED_FIELDS:
                field_horizon = horizon - 6 if variable == "10_m_v_component_of_wind" else horizon
                row: dict[str, object] = {
                    "origin": [origin], "forecast_type": [forecast_type],
                    "year": ["2016"], "month": ["07"], "day": ["01"], "time": ["00:00"],
                    "variable": [variable], "level_type": [level_type],
                    "leadtime_hour": [str(value) for value in range(0, field_horizon + 1, 6)],
                }
                if level_value is not None:
                    row["level_value"] = [level_value]
                output.append(row)
            return output

        constraints = rows("jma", "control_forecast", 264) + rows("jma", "perturbed_forecast", 264)
        availability = TiggeAvailability(constraints)
        steps = availability.available_steps("tigge-jma", datetime(2016, 7, 1, tzinfo=UTC))
        self.assertEqual(steps, list(range(0, 259, 6)))
        self.assertEqual(availability.available_steps("tigge-cma", datetime(2016, 7, 1, tzinfo=UTC)), [])

        bom = rows("bom", "control_forecast", 240) + rows("bom", "perturbed_forecast", 240)
        for row in bom:
            if row["level_type"] == ["single_level"]:
                row["leadtime_hour"] = row["leadtime_hour"][1:]
        self.assertEqual(
            TiggeAvailability(bom).available_steps("tigge-bom", datetime(2016, 7, 1, tzinfo=UTC)),
            list(range(6, 235, 6)),
        )

    def test_all_tigge_centres_have_public_model_metadata(self) -> None:
        self.assertEqual(set(TIGGE_MODEL_IDS), set(TIGGE_CENTRES))
        for model in TIGGE_MODEL_IDS:
            definition = MODEL_DEFINITIONS[model]
            self.assertIn("TIGGE", definition.label)
            self.assertIn(definition.licence, {"CC BY 4.0", "CC BY-NC 4.0"})

    def test_manifest_entry_horizon_is_derived_from_valid_end(self) -> None:
        entry = {"cycle_utc": "2026-08-30T00:00:00Z", "valid_end_utc": "2026-09-15T00:00:00Z"}
        self.assertEqual(manifest_entry_horizon_hours(entry), 384)

    def test_noaa_legacy_cloud_layouts_and_ensemble_sizes(self) -> None:
        gfs = NcepAdapter("gfs", client=RecordingClient())
        old_gfs, unused = gfs._urls(datetime(2021, 3, 21, 12, tzinfo=UTC), 120)
        new_gfs, unused = gfs._urls(datetime(2021, 3, 22, 12, tzinfo=UTC), 120)
        self.assertNotIn("/atmos/", old_gfs)
        self.assertIn("/atmos/", new_gfs)

        gefs = NcepAdapter("gefs", client=RecordingClient())
        root_layout, unused = gefs._urls(datetime(2017, 1, 1, tzinfo=UTC), 6, "c00")
        folder_layout, unused = gefs._urls(datetime(2019, 1, 1, tzinfo=UTC), 6, "c00")
        current_layout, unused = gefs._urls(datetime(2026, 8, 30, tzinfo=UTC), 6, "c00")
        self.assertTrue(root_layout.endswith("pgrb2af006"))
        self.assertTrue(folder_layout.endswith("pgrb2a/gec00.t00z.pgrb2af06"))
        self.assertTrue(current_layout.endswith("pgrb2a.0p50.f006"))
        self.assertEqual(len(gefs._member_ids(datetime(2019, 1, 1, tzinfo=UTC), None)), 21)
        self.assertEqual(len(gefs._member_ids(datetime(2026, 1, 1, tzinfo=UTC), None)), 31)

    def test_tigge_ncep_uses_noaa_from_2017_without_splitting_model_identity(self) -> None:
        adapter = adapter_for("tigge-ncep", workers=3)
        self.assertIsInstance(adapter, TiggeNcepAdapter)
        source_payload = {
            "model": {"id": "gefs"},
            "source": {},
            "model_version": {"label": "GEFS v11.3"},
            "qa": {},
        }
        with (
            patch.object(adapter.noaa, "build", return_value=source_payload) as noaa_build,
            patch.object(adapter.ecds, "build") as ecds_build,
            patch("forecast_pipeline.sources.validate_cycle_payload", return_value={"status": "passed", "errors": []}),
        ):
            payload = adapter.build("2017010200", [0, 6], member_limit=1)
        noaa_build.assert_called_once_with("2017010200", [0, 6], member_limit=1)
        ecds_build.assert_not_called()
        self.assertEqual(payload["model"]["id"], "tigge-ncep")
        self.assertEqual(payload["model_version"]["label"], "GEFS v11.3")
        self.assertEqual(payload["source"]["service"], "NOAA Open Data GEFS archive")

    def test_tigge_ncep_keeps_pre_2017_cycles_on_ecds(self) -> None:
        adapter = TiggeNcepAdapter(workers=2)
        source_payload = {"model": {"id": "tigge-ncep"}}
        with (
            patch.object(adapter.ecds, "build", return_value=source_payload) as ecds_build,
            patch.object(adapter.noaa, "build") as noaa_build,
        ):
            payload = adapter.build("2016123100", [0, 6], member_limit=1)
        ecds_build.assert_called_once_with("2016123100", [0, 6], member_limit=1)
        noaa_build.assert_not_called()
        self.assertIs(payload, source_payload)

    def test_trailing_precipitation_uses_24_hour_difference(self) -> None:
        cumulative = np.asarray([0, 3, 8, 13, 20, 31], dtype=np.float32)[:, None, None]
        result = trailing_24h(cumulative, [0, 6, 12, 18, 24, 30])[:, 0, 0]
        np.testing.assert_allclose(result, [0, 3, 8, 13, 20, 28])

    def test_weather_encoding_is_deterministic_and_non_negative(self) -> None:
        shape = (2, 2, 2)
        weather = compact_weather(
            np.asarray([-2, 0, 1, 4, 8, 16, 32, np.nan], dtype=np.float32).reshape(shape),
            np.asarray([-3, 0, 0.5, 4, 16, 64, 128, np.nan], dtype=np.float32).reshape(shape),
            "test",
        )
        raw = gzip.decompress(base64.b64decode(weather["vorticity"]["data"]))
        values = np.frombuffer(raw, dtype=np.uint8).reshape(shape) * weather["vorticity"]["scale"]
        self.assertEqual(float(values.min()), 0.0)
        self.assertEqual(float(values.max()), 31.875)

    def test_payload_validator_rejects_duplicate_track_steps(self) -> None:
        payload = {
            "steps": [0, 6],
            "members": {"available": 1, "expected": 1},
            "tracks": [{"id": "x", "points": [[0, 80, 20], [0, 81, 21]]}],
            "systems": [],
            "weather": {
                "vorticity": {"shape": [2, len(GRID_LATS), len(GRID_LONS)]},
                "precipitation": {"shape": [2, len(GRID_LATS), len(GRID_LONS)]},
            },
        }
        result = validate_cycle_payload(payload)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("unique and increasing" in item for item in result["errors"]))

    def test_physical_support_run_breaks_across_false_or_missing_hours(self) -> None:
        mask = np.asarray([True, True, False, True, True, True, True])
        steps = np.asarray([0, 1, 2, 3, 4, 6, 7])
        self.assertEqual(_longest_true_run(mask, steps), 2)

    def test_forecast_interpolation_can_begin_after_initialization(self) -> None:
        np.testing.assert_array_equal(_hourly_axis([6, 12]), np.arange(6, 13))
        source = np.asarray([6.0, 12.0], dtype=np.float32)[:, None, None]
        np.testing.assert_allclose(interpolate_hourly(source, [6, 12])[:, 0, 0], np.arange(6.0, 13.0))

    def test_atomic_manifest_is_publicly_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "manifest.json"
            atomic_write_json(target, {"schema": "test"})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertTrue(stat.S_IMODE(target.parent.stat().st_mode) & 0o005)

    def test_progressive_publisher_ignores_a_stale_completed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            manifest = {
                "schema": "mla-forecast-manifest-v1",
                "tigge_archive": [{
                    "model": "tigge-ecmwf", "cycle": "2016070100",
                    "cycle_utc": "2016-07-01T00:00:00Z", "valid_end_utc": "2016-07-06T00:00:00Z",
                }],
                "tigge_backfill": {
                    "generated_utc": "2026-08-30T18:36:43Z",
                    "planned_cycles": 1,
                    "status": "complete",
                },
            }
            atomic_write_json(target / "manifest.json", manifest)
            available, status = target_state(
                target,
                "tigge_archive",
                "tigge_backfill",
                ("2026-08-30T19:59:22Z", 958),
            )
            self.assertEqual(available, {("tigge-ecmwf", "2016070100"): 120})
            self.assertEqual(status, "")

            plan = {
                "generated_utc": "2026-08-30T19:59:22Z",
                "cycles": [
                    {"model": "tigge-ecmwf", "cycle": "2006100100", "horizon_hours": 360},
                    {"model": "tigge-ecmwf", "cycle": "2006102412", "horizon_hours": 360},
                ],
            }
            update_progress(target, "tigge_archive", "tigge_backfill", plan)
            updated = json.loads((target / "manifest.json").read_text())
            self.assertEqual(updated["tigge_backfill"]["status"], "running")
            self.assertEqual(updated["tigge_backfill"]["planned_cycles"], 2)
            self.assertEqual(updated["tigge_backfill"]["available_cycles"], 0)

            manifest["tigge_backfill"] = {
                "generated_utc": "2026-08-30T19:59:22Z",
                "planned_cycles": 958,
                "status": "incomplete",
            }
            atomic_write_json(target / "manifest.json", manifest)
            unused, status = target_state(
                target,
                "tigge_archive",
                "tigge_backfill",
                ("2026-08-30T19:59:22Z", 958),
            )
            self.assertEqual(status, "incomplete")

    def test_recent_cycle_window_keeps_weather_cycles_for_48_hours(self) -> None:
        def entry(hours_ago: int) -> dict[str, object]:
            cycle = datetime(2026, 8, 30, 12, tzinfo=UTC) - timedelta(hours=hours_ago)
            return {
                "cycle": cycle.strftime("%Y%m%d%H"),
                "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
                "url": f"cycles/gfs/{cycle:%Y%m%d%H}.json.gz",
            }

        retained = replace_recent_entry([entry(54), entry(48), entry(42), entry(6)], entry(0))
        self.assertEqual([item["cycle"] for item in retained], [entry(0)["cycle"], entry(6)["cycle"], entry(42)["cycle"], entry(48)["cycle"]])

    def test_analysis_centres_average_t0_and_keep_six_hour_signature(self) -> None:
        payload = {
            "systems": [{"id": "S01", "member_count": 2, "track_ids": ["a", "b"]}],
            "tracks": [
                {
                    "id": "a",
                    "maximum_provisional_category": 1,
                    "points": [[0, 80, 20], [6, 79, 21], [12, 78, 22]],
                },
                {
                    "id": "b",
                    "maximum_provisional_category": 2,
                    "points": [[0, 82, 22], [6, 81, 23], [12, 80, 24]],
                },
            ],
        }
        centres = analysis_centres(payload)
        self.assertEqual(len(centres), 1)
        self.assertEqual((centres[0]["longitude"], centres[0]["latitude"]), (81.0, 21.0))
        self.assertEqual(centres[0]["peak_category"], 2)
        self.assertEqual(centres[0]["match_points"], [[0, 81.0, 21.0], [6, 80.0, 22.0], [12, 79.0, 23.0]])

    def test_analysis_history_retains_fourteen_days(self) -> None:
        newest = datetime(2026, 8, 30, 12, tzinfo=UTC)

        def entry(hours_ago: int) -> dict[str, object]:
            cycle = newest - timedelta(hours=hours_ago)
            return {
                "cycle": cycle.strftime("%Y%m%d%H"),
                "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
                "centres": [],
            }

        retained = replace_analysis_entry([entry(342), entry(336), entry(6)], entry(0))
        self.assertEqual(
            [item["cycle"] for item in retained],
            [entry(0)["cycle"], entry(6)["cycle"], entry(336)["cycle"]],
        )

    def test_archive_preserves_complete_axis_tracks_and_optional_weather(self) -> None:
        payload = {
            "schema": "mla-forecast-cycle-v1",
            "model": {"id": "gfs", "label": "GFS"},
            "cycle": "2026083000",
            "cycle_utc": "2026-08-30T00:00:00Z",
            "valid_times": ["2026-08-30T00:00:00Z", "2026-08-30T06:00:00Z"],
            "tracks": [{"id": "a", "points": [[0, 80, 20], [1, 81, 21]]}],
            "systems": [{"id": "s"}],
            "weather": {"basis": "deterministic", "vorticity": {"shape": [2, 2, 2]}},
            "tracking_qa": [{"internal": True}],
        }
        archived = archive_payload(payload, StubVerifier())
        self.assertNotIn("weather", archived)
        self.assertNotIn("tracking_qa", archived)
        self.assertEqual(archived["valid_times"], payload["valid_times"])
        self.assertEqual(archived["tracks"], payload["tracks"])
        self.assertEqual(archived["archive_coverage"]["valid_time_count"], 2)
        self.assertEqual(archived["archive_coverage"]["published_track_point_count"], 2)
        self.assertTrue(archived["archive_coverage"]["includes_zero_disturbance_cycles"])
        self.assertEqual(archive_manifest_entry(archived, "archive/gfs/x.json.gz")["analysis_centres"], [])

        operational = archive_payload(payload, StubVerifier(), include_weather=True)
        self.assertEqual(operational["weather"], payload["weather"])
        entry = archive_manifest_entry(operational, "archive/gfs/x.json.gz")
        self.assertEqual(entry["weather_fields"], ["vorticity"])
        self.assertNotIn("tracking_qa", operational)

    def test_model_version_crosswalk_uses_cycle_boundaries(self) -> None:
        before = model_version("gfs", datetime(2021, 3, 22, 11, tzinfo=UTC))
        after = model_version("gfs", datetime(2021, 3, 22, 12, tzinfo=UTC))
        current_gefs = model_version("gefs", datetime(2026, 8, 30, 0, tzinfo=UTC))
        self.assertEqual(before["label"], "GFS v15 family")
        self.assertEqual(after["label"], "GFS v16 family")
        self.assertEqual(current_gefs["label"], "GEFS v12.3.20")

    def test_tigge_version_crosswalk_covers_the_full_archive_plan(self) -> None:
        cycle = datetime(2006, 10, 1, 0, tzinfo=UTC)
        end = datetime(2016, 3, 18, 12, tzinfo=UTC)
        while cycle <= end:
            self.assertNotEqual(model_version("tigge-ecmwf", cycle)["label"], "Version not yet crosswalked")
            cycle += timedelta(hours=12)
        self.assertEqual(
            model_version("tigge-ecmwf", datetime(2008, 3, 11, 11, tzinfo=UTC))["label"],
            "IFS Cycle 32r3",
        )
        self.assertEqual(
            model_version("tigge-ecmwf", datetime(2008, 3, 11, 12, tzinfo=UTC))["label"],
            "IFS Cycle 32r3V",
        )
        self.assertEqual(
            model_version("tigge-ecmwf", datetime(2015, 5, 12, 12, tzinfo=UTC))["label"],
            "IFS Cycle 41r1",
        )


if __name__ == "__main__":
    unittest.main()
