#!/usr/bin/env python3
"""Fast contract tests for the static forecast data pipeline."""

from __future__ import annotations

import base64
import gzip
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from forecast_pipeline.forecast_core import (
    GRID_LATS,
    GRID_LONS,
    atomic_write_json,
    candidate_cycles,
    compact_weather,
    trailing_24h,
    validate_cycle_payload,
)
from forecast_pipeline.sources import _fetch_record, parse_ecmwf_index, parse_ncep_index
from forecast_pipeline.v56_tracking import _longest_true_run


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, int | None] | None]] = []

    def get(self, url: str, *, byte_range: tuple[int, int | None] | None = None) -> bytes:
        self.calls.append((url, byte_range))
        return b"GRIB"


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

    def test_atomic_manifest_is_publicly_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "manifest.json"
            atomic_write_json(target, {"schema": "test"})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertTrue(stat.S_IMODE(target.parent.stat().st_mode) & 0o005)


if __name__ == "__main__":
    unittest.main()
