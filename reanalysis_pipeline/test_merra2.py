from __future__ import annotations

import unittest
from datetime import date

from .merra2 import constraint, granule_name, stream_number


class Merra2RequestTest(unittest.TestCase):
    def test_stream_boundaries(self) -> None:
        self.assertEqual(stream_number(date(1991, 12, 31)), 100)
        self.assertEqual(stream_number(date(1992, 1, 1)), 200)
        self.assertEqual(stream_number(date(2001, 1, 1)), 300)
        self.assertEqual(stream_number(date(2011, 1, 1)), 400)

    def test_granule_name(self) -> None:
        self.assertEqual(
            granule_name("pressure", date(2016, 7, 1)),
            "M2I3NPASM.5.12.4:MERRA2_400.inst3_3d_asm_Np.20160701.nc4",
        )

    def test_pressure_constraint_contains_required_fields(self) -> None:
        value = constraint("pressure")
        for name in ("/U[", "/V[", "/T[", "/RH["):
            self.assertIn(name, value)
        self.assertIn("/lev[6:2:16]", value)

    def test_surface_constraint_is_three_hourly(self) -> None:
        value = constraint("surface")
        self.assertIn("/time[0:3:21]", value)
        for name in ("/U10M[", "/V10M[", "/SLP[", "/PS["):
            self.assertIn(name, value)


if __name__ == "__main__":
    unittest.main()

