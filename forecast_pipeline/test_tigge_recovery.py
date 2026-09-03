#!/usr/bin/env python3
"""Contract tests for resumable ECDS TIGGE recovery."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from forecast_pipeline.recover_tigge_jobs import (
    cached_result_valid,
    cycle_from_request,
    inspect_job,
    is_full_cycle_request,
    normalized_md5,
    recent_successful_jobs,
    staged_cycle_complete,
    write_jobs,
)


class TiggeRecoveryTests(unittest.TestCase):
    def test_ecds_md5_is_left_padded_when_leading_zero_is_omitted(self) -> None:
        self.assertEqual(normalized_md5("abc"), "0" * 29 + "abc")
        self.assertEqual(normalized_md5(""), "")

    def test_multi_date_diagnostic_request_is_not_a_cycle(self) -> None:
        self.assertIsNone(
            cycle_from_request(
                {
                    "date": ["2025-10-27", "2025-10-23"],
                    "time": "12:00:00",
                }
            )
        )

    def test_component_probe_is_not_a_recoverable_whole_cycle(self) -> None:
        full = {
            "param": "131/132/151/165/166/228",
            "levtype": ["pl", "sfc"],
            "levelist": "500/700/850",
            "type": ["cf", "pf"],
            "step": "/".join(str(value) for value in range(0, 241, 6)),
        }
        self.assertTrue(is_full_cycle_request(full, "tigge-imd"))
        probe = dict(full, param="131/132", levtype="pl")
        self.assertFalse(is_full_cycle_request(probe, "tigge-imd"))
        short = dict(full, step="0/6")
        self.assertFalse(is_full_cycle_request(short, "tigge-imd"))

    def test_recent_jobs_obey_inclusive_cutoff(self) -> None:
        payload = {
            "jobs": [
                {"jobID": "new", "created": "2026-09-02T00:00:00"},
                {"jobID": "edge", "created": "2026-09-01T00:00:00"},
                {"jobID": "old", "created": "2026-08-31T23:59:59"},
            ],
            "links": [{"rel": "next", "href": "https://unused.invalid/next"}],
        }
        with patch(
            "forecast_pipeline.recover_tigge_jobs.get_json", return_value=payload
        ) as get_json:
            jobs = recent_successful_jobs(
                "secret",
                created_after=datetime(2026, 9, 1, tzinfo=UTC),
            )
        self.assertEqual([job["jobID"] for job in jobs], ["new", "edge"])
        get_json.assert_called_once()

    def test_inspection_maps_archive_origin_and_result(self) -> None:
        receipt = {
            "collection-id": "tigge-forecasts",
            "created-at": "2026-09-02T01:00:00",
            "finished-at": "2026-09-02T06:00:00",
            "request": {
                "origin": "dems",
                "date": "2023-06-19",
                "time": "00:00:00",
                "param": "131/132/151/165/166/228",
                "levtype": ["pl", "sfc"],
                "levelist": "500/700/850",
                "type": ["cf", "pf"],
                "step": "/".join(str(value) for value in range(0, 241, 6)),
            },
        }
        result = {
            "asset": {
                "value": {
                    "href": "https://cache.invalid/result.grib",
                    "file:checksum": "abc",
                    "file:size": 123,
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "forecast_pipeline.recover_tigge_jobs.get_json",
                side_effect=[receipt, result],
            ):
                record = inspect_job(
                    {"jobID": "job-1"},
                    "secret",
                    wanted_models={"tigge-ncmrwf"},
                    public_root=Path(directory),
                )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["model"], "tigge-ncmrwf")
        self.assertEqual(record["cycle"], "2023061900")
        self.assertEqual(record["size"], 123)
        self.assertFalse(record["published"])

    def test_published_cycle_does_not_fetch_expiring_result(self) -> None:
        receipt = {
            "collection-id": "tigge-forecasts",
            "created-at": "2026-09-02T01:00:00",
            "finished-at": "2026-09-02T06:00:00",
            "request": {
                "origin": "vabb",
                "date": "2023-06-19",
                "time": "00:00:00",
                "param": "131/132/151/165/166/228",
                "levtype": ["pl", "sfc"],
                "levelist": "500/700/850",
                "type": ["cf", "pf"],
                "step": "/".join(str(value) for value in range(0, 241, 6)),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            asset = public / "tigge" / "tigge-imd" / "2023061900.json.gz"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"published")
            with patch(
                "forecast_pipeline.recover_tigge_jobs.get_json",
                return_value=receipt,
            ) as get_json:
                record = inspect_job(
                    {"jobID": "job-1"},
                    "secret",
                    wanted_models={"tigge-imd"},
                    public_root=public,
                )
        self.assertTrue(record["published"])
        self.assertEqual(get_json.call_count, 1)

    def test_cache_qa_and_processing_job_table(self) -> None:
        content = b"GRIB-result"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "all.grib"
            path.write_bytes(content)
            record = {
                "model": "tigge-imd",
                "cycle": "2023061900",
                "size": len(content),
                "checksum": hashlib.md5(content, usedforsecurity=False).hexdigest(),
            }
            self.assertTrue(cached_result_valid(path, record))
            record["size"] += 1
            self.assertFalse(cached_result_valid(path, record))

            jobs = root / "jobs.tsv"
            count = write_jobs(
                jobs,
                [
                    {"model": "tigge-ncmrwf", "cycle": "2023061900"},
                    {"model": "tigge-imd", "cycle": "2023061900"},
                    {"model": "tigge-imd", "cycle": "2023061900"},
                ],
            )
            self.assertEqual(count, 2)
            self.assertEqual(
                jobs.read_text(encoding="utf-8").splitlines(),
                [
                    "1\ttigge-imd\t2023061900\t240\t0",
                    "2\ttigge-ncmrwf\t2023061900\t240\t0",
                ],
            )

    def test_staged_cycle_requires_matching_tigge_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "tigge-imd-2023061900" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                '{"tigge_archive":[{"model":"tigge-imd","cycle":"2023061900"}]}',
                encoding="utf-8",
            )
            self.assertTrue(
                staged_cycle_complete(root, "tigge-imd", "2023061900")
            )
            self.assertFalse(
                staged_cycle_complete(root, "tigge-ncmrwf", "2023061900")
            )


if __name__ == "__main__":
    unittest.main()
