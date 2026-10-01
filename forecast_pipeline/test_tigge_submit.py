#!/usr/bin/env python3
"""Contract tests for non-blocking ECDS TIGGE submission."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from forecast_pipeline.sources import TiggeAdapter
from forecast_pipeline.submit_tigge_requests import pump
from forecast_pipeline.prioritise_tigge_cases import merge_cases


class FakeJobs:
    def __init__(self, jobs: list[dict[str, str]]):
        self.json = {"jobs": jobs}


class FakeRemote:
    def __init__(self, request_id: str):
        self.request_id = request_id


class FakeClient:
    def __init__(self, jobs: list[dict[str, str]], receipts: dict[str, dict]):
        self.jobs = jobs
        self.receipts = receipts
        self.submissions: list[tuple[str, dict]] = []
        self.job_kwargs: dict = {}

    def get_jobs(self, **kwargs):
        self.job_kwargs = kwargs
        return FakeJobs(self.jobs)

    def get_receipt(self, request_id: str):
        return self.receipts[request_id]

    def submit(self, collection_id: str, request: dict):
        self.submissions.append((collection_id, request))
        return FakeRemote(f"new-{len(self.submissions)}")


class TiggeSubmissionTests(unittest.TestCase):
    def test_priority_precedes_cursor_and_keeps_background_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycles = [
                {"model": "tigge-ecmwf", "cycle": cycle, "horizon_hours": 24, "first_step_hours": 0}
                for cycle in ("2016062700", "2016062800", "2016070100", "2025071200")
            ]
            cycles[2].update(priority_rank=0, priority_cases=["INCOMPASS"])
            cycles[3].update(priority_rank=1, priority_cases=["India 2025"])
            plan = root / "plan.json"
            plan.write_text(json.dumps({"models": ["tigge-ecmwf"], "cycles": cycles}))
            state = root / "ecds-submit-state.json"
            state.write_text(json.dumps({"schema": "mla-ecds-tigge-submit-state-v1", "cursor": 1, "attempts": []}))
            client = FakeClient([], {})
            result = pump(plan, root, root / "public", client, max_submissions=2)
            self.assertEqual([request["date"] for _, request in client.submissions], ["2016-07-01", "2025-07-12"])
            self.assertEqual(result["eligible_priority_cycles"], 2)
            self.assertEqual(result["cursor_next"], 1)
            # Active priorities must not block the ordinary backlog or submit twice.
            client.jobs = [{"jobID": "new-1", "status": "accepted"}, {"jobID": "new-2", "status": "running"}]
            result = pump(plan, root, root / "public", client, max_submissions=1)
            self.assertEqual(result["skipped"]["active"], 2)
            self.assertEqual(client.submissions[-1][1]["date"], "2016-06-28")
            self.assertEqual(result["cursor_next"], 2)

    def test_case_merge_interleaves_and_preserves_existing_indices(self) -> None:
        class Availability:
            def available_steps(self, model, cycle):
                return [] if model == "tigge-imd" and cycle.year < 2020 else list(range(0, 25, 6))

        original = {"models": ["tigge-ecmwf"], "cycles": [
            {"model": "tigge-ecmwf", "cycle": "2016070100", "horizon_hours": 24}
        ]}
        cases = [
            dict(name="INCOMPASS", start="2016063012", end="2016070112", anchor="2016070100"),
            dict(name="India2025", start="2025071112", end="2025071212", anchor="2025071200"),
        ]
        result = merge_cases(original, cases, ["tigge-imd", "tigge-ecmwf"], Availability())
        self.assertEqual(result["cycles"][0]["cycle"], original["cycles"][0]["cycle"])
        self.assertNotIn("priority_rank", original["cycles"][0])
        ranked = sorted(result["cycles"], key=lambda item: item["priority_rank"])
        self.assertEqual([(item["model"], item["cycle"]) for item in ranked[:4]], [
            ("tigge-ecmwf", "2016070100"), ("tigge-imd", "2025071200"),
            ("tigge-ecmwf", "2016063012"), ("tigge-ecmwf", "2025071200"),
        ])
        again = merge_cases(result, cases, ["tigge-imd", "tigge-ecmwf"], Availability())
        self.assertEqual(again["cycles"], result["cycles"])
        self.assertEqual(len(result["cycles"]), 9)

    def test_ecds_request_contains_one_complete_cycle(self) -> None:
        adapter = TiggeAdapter("tigge-imd", workers=1)
        request = adapter.ecds_request(
            datetime(2023, 7, 1, tzinfo=UTC),
            list(range(0, adapter.centre.maximum_horizon_hours + 1, 6)),
        )
        self.assertEqual(request["origin"], adapter.centre.archive_origin)
        self.assertEqual(request["levtype"], ["pl", "sfc"])
        self.assertEqual(request["levelist"], "500/700/850")
        self.assertEqual(request["type"], list(adapter.centre.forecast_types))
        self.assertTrue({"131", "132", "151", "165", "166", "228"}.issubset(set(request["param"].split("/"))))

    def test_pump_respects_account_capacity_and_deduplicates_active_cycle(self) -> None:
        now = datetime(2026, 9, 7, 10, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            public_root = root / "public"
            run_root.mkdir()
            public_root.mkdir()
            adapter = TiggeAdapter("tigge-imd", workers=1)
            horizon = adapter.centre.maximum_horizon_hours
            plan = {
                "schema": "mla-forecast-tigge-plan-v1",
                "generated_utc": "2026-09-01T00:00:00Z",
                "models": ["tigge-imd"],
                "cycles": [
                    {
                        "model": "tigge-imd",
                        "cycle": cycle,
                        "first_step_hours": 0,
                        "horizon_hours": horizon,
                        "valid_time_count": horizon // 6 + 1,
                    }
                    for cycle in ("2023090300", "2023090100", "2023083000")
                ],
            }
            plan_path = run_root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            (public_root / "manifest.json").write_text(
                json.dumps({
                    "schema": "mla-forecast-manifest-v1",
                    "tigge_archive": [{"model": "tigge-imd", "cycle": "2023090300"}],
                }),
                encoding="utf-8",
            )
            active_request = adapter.ecds_request(
                datetime(2023, 9, 1, tzinfo=UTC), list(range(0, horizon + 1, 6))
            )
            client = FakeClient(
                [
                    {"jobID": "active-target", "status": "running"},
                    {"jobID": "active-other", "status": "accepted"},
                ],
                {
                    "active-target": {
                        "collection-id": "tigge-forecasts",
                        "request": active_request,
                    },
                    "active-other": {
                        "collection-id": "reanalysis-era5-single-levels",
                        "request": {},
                    },
                },
            )
            (run_root / "ecds-submit-state.json").write_text(
                json.dumps({
                    "schema": "mla-ecds-tigge-submit-state-v1",
                    "attempts": [{
                        "model": "tigge-imd",
                        "cycle": "2023090100",
                        "job_id": "active-target",
                        "submitted_utc": "2026-09-07T09:00:00Z",
                        "status": "accepted",
                    }],
                    "events": [],
                }),
                encoding="utf-8",
            )
            summary = pump(
                plan_path,
                run_root,
                public_root,
                client,
                capacity=3,
                now=now,
            )
            self.assertEqual(summary["account_active_jobs"], 2)
            self.assertEqual(summary["account_queued_jobs"], 1)
            self.assertEqual(summary["active_selected_cycles"], 1)
            self.assertEqual(summary["submitted"], 1)
            self.assertEqual(summary["skipped"]["published"], 1)
            self.assertEqual(summary["skipped"]["active"], 1)
            self.assertEqual(client.submissions[0][0], "tigge-forecasts")
            self.assertEqual(client.submissions[0][1]["date"], "2023-08-30")
            self.assertEqual(client.job_kwargs["status"], ["accepted", "running"])
            state = json.loads((run_root / "ecds-submit-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["attempts"][-1]["job_id"], "new-1")


if __name__ == "__main__":
    unittest.main()
