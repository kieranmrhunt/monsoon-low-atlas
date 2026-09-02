from __future__ import annotations

import unittest

from .pump_imdaa import is_canary


class ImdaaPumpTest(unittest.TestCase):
    def test_one_day_request_is_canary(self) -> None:
        self.assertTrue(is_canary({"days": ["01"]}))

    def test_month_request_is_not_canary(self) -> None:
        self.assertFalse(is_canary({"days": [f"{day:02d}" for day in range(1, 32)]}))


if __name__ == "__main__":
    unittest.main()
