from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import xarray as xr

from .merra2 import COLLECTIONS, constraint, download_days, granule_name, output_path, stream_number, validate_download


class Merra2RequestTest(unittest.TestCase):
    def test_stream_boundaries(self) -> None:
        self.assertEqual(stream_number(date(1991, 12, 31)), 100)
        self.assertEqual(stream_number(date(1992, 1, 1)), 200)
        self.assertEqual(stream_number(date(2001, 1, 1)), 300)
        self.assertEqual(stream_number(date(2011, 1, 1)), 400)
        self.assertEqual(stream_number(date(2020, 8, 31)), 400)
        self.assertEqual(stream_number(date(2020, 9, 1)), 401)
        self.assertEqual(stream_number(date(2020, 9, 30)), 401)
        self.assertEqual(stream_number(date(2020, 10, 1)), 400)
        self.assertEqual(stream_number(date(2021, 5, 31)), 400)
        self.assertEqual(stream_number(date(2021, 6, 1)), 401)
        self.assertEqual(stream_number(date(2021, 9, 30)), 401)
        self.assertEqual(stream_number(date(2021, 10, 1)), 400)

    def test_granule_name(self) -> None:
        self.assertEqual(
            granule_name("pressure", date(2016, 7, 1)),
            "M2I3NPASM.5.12.4:MERRA2_400.inst3_3d_asm_Np.20160701.nc4",
        )
        self.assertEqual(
            granule_name("pressure", date(2021, 6, 1)),
            "M2I3NPASM.5.12.4:MERRA2_401.inst3_3d_asm_Np.20210601.nc4",
        )
        for kind, collection in COLLECTIONS.items():
            self.assertIn(f"MERRA2_401.{collection['granule_product']}.20200901", granule_name(kind, date(2020, 9, 1)))

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

    def test_precipitation_constraint_is_hourly(self) -> None:
        value = constraint("precipitation")
        self.assertIn("/time[0:1:23]", value)
        self.assertIn("/PRECTOT[0:1:23]", value)
        self.assertEqual(COLLECTIONS["precipitation"]["concept_id"], "C1276812838-GES_DISC")

    def test_cached_read_error_is_retried_without_deleting_input(self) -> None:
        day = date(2020, 9, 1)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = output_path(root, "pressure", day)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"original invalid response")
            with patch("reanalysis_pipeline.merra2.validate_download", side_effect=OSError("bad HDF5 chunk")), patch(
                "reanalysis_pipeline.merra2.EarthdataSession.login"
            ) as login:
                login.return_value.download.side_effect = RuntimeError("transfer failed")
                with self.assertRaisesRegex(RuntimeError, "transfer failed"):
                    download_days(root, root / "ledger.json", [day], kinds=["pressure"])
                login.return_value.download.assert_called_once_with("pressure", day, path)
                self.assertEqual(path.read_bytes(), b"original invalid response")

    def test_precipitation_payload_and_full_time_axis_are_validated(self) -> None:
        day = date(2020, 9, 1)
        times = np.datetime64(day) + np.timedelta64(30, "m") + np.arange(24) * np.timedelta64(1, "h")
        dataset = xr.Dataset(
            {"PRECTOT": (("time", "lat", "lon"), np.ones((24, 121, 121), dtype=np.float32))},
            coords={"time": times, "lat": np.linspace(-15, 45, 121), "lon": np.linspace(45, 120, 121)},
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "response.nc4"
            dataset.to_netcdf(path, engine="h5netcdf")
            validate_download("precipitation", day, path)
            wrong_times = times.copy()
            wrong_times[5] = times[4]
            dataset.assign_coords(time=wrong_times).to_netcdf(path, engine="h5netcdf")
            with self.assertRaisesRegex(ValueError, "timestamps"):
                validate_download("precipitation", day, path)
            dataset.PRECTOT.values[5] = np.nan
            dataset.to_netcdf(path, engine="h5netcdf")
            with self.assertRaisesRegex(ValueError, "entirely missing"):
                validate_download("precipitation", day, path)

    def test_small_error_page_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "response.nc4"
            path.write_bytes(b"<html>Authentication required</html>")
            with self.assertRaisesRegex(ValueError, "unexpectedly small"):
                validate_download("pressure", date(2020, 9, 1), path)


if __name__ == "__main__":
    unittest.main()
