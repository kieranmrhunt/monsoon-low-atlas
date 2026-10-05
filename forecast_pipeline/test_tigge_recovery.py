#!/usr/bin/env python3
"""Contract tests for resumable ECDS TIGGE recovery."""

from __future__ import annotations

import hashlib
from copy import deepcopy
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from forecast_pipeline.recover_tigge_jobs import (
    cached_result_valid,
    cycle_from_request,
    download_result,
    inspect_job,
    is_full_cycle_request,
    normalized_md5,
    multipart_metadata_valid,
    recent_successful_jobs,
    resolve_download_checksum,
    staged_cycle_complete,
    unresolved_errors,
    write_jobs,
)


class TiggeRecoveryTests(unittest.TestCase):
    @staticmethod
    def multipart_fixture():
        chunks = [b"GRIB-first-part", b"different-second-part", b"7777"]
        parts = [
            {"size": len(chunk), "md5": hashlib.md5(chunk, usedforsecurity=False).hexdigest()}
            for chunk in chunks
        ]
        digest = hashlib.md5(b"".join(bytes.fromhex(p["md5"]) for p in parts), usedforsecurity=False).hexdigest()
        record = {
            "model": "tigge-ecmwf", "cycle": "2016070112",
            "asset_url": "https://cache.invalid/result.grib",
            "size": sum(p["size"] for p in parts), "checksum": digest,
        }
        return chunks, parts, record

    @staticmethod
    def response(headers, chunks=()):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = headers
        response.iter_content.return_value = iter(chunks)
        return response

    def test_multipart_metadata_uses_actual_ordered_part_boundaries(self):
        chunks, parts, record = self.multipart_fixture()
        def head(url, *, params, timeout):
            part = parts[params["partNumber"] - 1]
            return self.response({"ETag": f'"{part["md5"]}"', "Content-Length": str(part["size"]), "x-amz-mp-parts-count": "3"})
        with patch("forecast_pipeline.recover_tigge_jobs.requests.head", side_effect=head) as query:
            resolve_download_checksum(record, {"ETag": f'"{record["checksum"]}-3"'})
            self.assertEqual(query.call_count, 3)
        self.assertEqual(record["checksum_parts"], parts)
        self.assertTrue(multipart_metadata_valid(record))
        # Offline revalidation checks all bytes, not just the response headers.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all.grib"
            content = b"".join(chunks)
            path.write_bytes(content)
            self.assertTrue(cached_result_valid(path, record))
            for index in [0, len(chunks[0]), len(content)-1]:
                damaged = bytearray(content); damaged[index] ^= 1
                path.write_bytes(damaged)
                self.assertFalse(cached_result_valid(path, record))
            for damaged in [content[:-1], content+b"x"]:
                path.write_bytes(damaged)
                self.assertFalse(cached_result_valid(path, record))
            path.write_bytes(content)
            wrong = deepcopy(record); wrong["checksum_parts"][0]["size"] += 1; wrong["checksum_parts"][1]["size"] -= 1
            self.assertFalse(cached_result_valid(path, wrong))
            wrong = deepcopy(record); wrong["checksum_parts"].reverse()
            self.assertFalse(cached_result_valid(path, wrong))
        with patch("forecast_pipeline.recover_tigge_jobs.requests.head") as query:
            resolve_download_checksum(record, {"ETag": f'"{record["checksum"]}-3"'})
            query.assert_not_called()

    def test_multipart_rejects_untrusted_or_incomplete_part_metadata(self):
        _, parts, original = self.multipart_fixture()
        for fault in ["etag", "size", "count", "multipart-part", "missing"]:
            record = deepcopy(original)
            def head(url, *, params, timeout):
                part = parts[params["partNumber"] - 1]
                headers = {"ETag": part["md5"], "Content-Length": str(part["size"]), "x-amz-mp-parts-count": "3"}
                if fault == "etag": headers["ETag"] = "0"*32
                if fault == "size": headers["Content-Length"] = str(part["size"]+1)
                if fault == "count": headers["x-amz-mp-parts-count"] = "2"
                if fault == "multipart-part": headers["ETag"] += "-3"
                if fault == "missing": headers.pop("ETag")
                return self.response(headers)
            with self.subTest(fault=fault), patch("forecast_pipeline.recover_tigge_jobs.requests.head", side_effect=head):
                with self.assertRaises(RuntimeError):
                    resolve_download_checksum(record, {"ETag": f'"{record["checksum"]}-3"'})
                self.assertNotIn("checksum_parts", record)
        with self.assertRaises(RuntimeError):
            resolve_download_checksum(original, {"ETag": '"'+'0'*32+'-3"'})

    def test_multipart_download_checks_bytes_before_promotion(self):
        chunks, parts, original = self.multipart_fixture()
        def head(url, *, params, timeout):
            part = parts[params["partNumber"] - 1]
            return self.response({"ETag": part["md5"], "Content-Length": str(part["size"]), "x-amz-mp-parts-count": "3"})
        for corrupt in [False, True]:
            record = deepcopy(original); content = b"".join(chunks)
            if corrupt: content = b"X" + content[1:]
            # Transport chunks deliberately differ from object-store parts.
            response = self.response({"ETag": f'"{record["checksum"]}-3"'}, [content[:3],content[3:]])
            with tempfile.TemporaryDirectory() as directory, patch("forecast_pipeline.recover_tigge_jobs.requests.get",return_value=response), patch("forecast_pipeline.recover_tigge_jobs.requests.head",side_effect=head):
                root = Path(directory)
                if corrupt:
                    with self.assertRaisesRegex(RuntimeError, "failed QA"):
                        download_result(record, root, attempts=1)
                    self.assertFalse(list(root.rglob("all.grib")))
                else:
                    target = download_result(record, root, attempts=1)
                    self.assertTrue(cached_result_valid(target, record))
                    self.assertEqual(target.read_bytes(), content)
                self.assertFalse(list(root.rglob("*.part")))

    def test_success_resolves_old_errors_and_failures_do_not_accumulate(self):
        errors = [{"job_id":"a","message":"old"},{"job_id":"b","message":"old"},{"job_id":"b","message":"latest"},{"job_id":"c","message":"old"}]
        self.assertEqual(unresolved_errors([{"job_id":"a","published":True}], errors, [{"job_id":"c"}]), [{"job_id":"b","message":"latest"}])

    def test_single_part_md5_is_still_checked_and_unknown_algorithms_fail(self):
        content = b"GRIB-result"
        record = {"size": len(content), "checksum": hashlib.md5(content, usedforsecurity=False).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all.grib"
            path.write_bytes(content)
            with patch("forecast_pipeline.recover_tigge_jobs.requests.head") as head:
                resolve_download_checksum(record, {"ETag": f'"{record["checksum"]}"'})
                head.assert_not_called()
            self.assertTrue(cached_result_valid(path, record))
            self.assertFalse(cached_result_valid(path, dict(record, checksum_algorithm="unknown")))
            self.assertFalse(cached_result_valid(path, dict(record, checksum_algorithm="s3-multipart-md5")))
            path.write_bytes(b"X" + content[1:])
            self.assertFalse(cached_result_valid(path, record))

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
