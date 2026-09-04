import unittest

import numpy as np
import pandas as pd

from .impact import _monthly_control_excess, native_components
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


if __name__ == "__main__":
    unittest.main()
