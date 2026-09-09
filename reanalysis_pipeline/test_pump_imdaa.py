from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from .pump_imdaa import is_canary, pump


class ImdaaPumpTest(unittest.TestCase):
    def test_one_day_request_is_canary(self) -> None:
        self.assertTrue(is_canary({"days": ["01"]}))

    def test_month_request_is_not_canary(self) -> None:
        self.assertFalse(is_canary({"days": [f"{day:02d}" for day in range(1, 32)]}))

    def test_pump_reuses_one_authenticated_client(self) -> None:
        client = object()
        ledger = {
            "requests": {
                "first": {"days": ["01"], "sha256": "a", "status": "complete"},
                "second": {"days": ["02"], "sha256": "b", "status": "complete"},
            }
        }
        with (
            patch("reanalysis_pipeline.pump_imdaa.refresh_status", return_value={"complete": 2}) as status,
            patch("reanalysis_pipeline.pump_imdaa.download_completed", return_value=0) as download,
            patch("reanalysis_pipeline.pump_imdaa.read_ledger", return_value=ledger),
            patch("reanalysis_pipeline.pump_imdaa.submit_requests", return_value=1) as submit,
        ):
            result = pump(Path("ledger.json"), Path("raw"), maximum_active=8, client=client)
        status.assert_called_once_with(Path("ledger.json"), client=client)
        download.assert_called_once_with(
            Path("ledger.json"), Path("raw"), maximum=8, client=client
        )
        self.assertIs(submit.call_args.kwargs["client"], client)
        self.assertEqual(result["submitted"], 1)


if __name__ == "__main__":
    unittest.main()
