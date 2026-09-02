from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
import xarray as xr

from .standardise_jra55 import (
    PRECIPITATION_DATASET,
    PRECIPITATION_VARIABLE,
    _canonical_pressure_dimensions,
    _sample_field,
    ncss_url,
    precipitation_hourly,
    raw_paths,
    validate_raw_file,
)


class Jra55StandardisationTest(unittest.TestCase):
    def test_raw_validation_rejects_a_response_from_the_wrong_month(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "surface.nc"
            dataset = xr.Dataset(
                {"Pressure_surface": (("time", "latitude", "longitude"), np.ones((2, 1, 1), dtype=np.float32))},
                coords={
                    "time": [pd.Timestamp("1983-01-01"), pd.Timestamp("1983-02-01")],
                    "latitude": [20.0],
                    "longitude": [80.0],
                },
            )
            dataset.to_netcdf(path)
            with self.assertRaisesRegex(ValueError, "starts at"):
                validate_raw_file(
                    path,
                    ("Pressure_surface",),
                    pd.Timestamp("1980-01-01"),
                    pd.Timestamp("1980-02-01"),
                )

    def test_sample_field_drops_conflicting_scalar_metadata_coordinates(self) -> None:
        dataset = xr.Dataset(
            {"field": (("time", "latitude", "longitude"), np.ones((1, 2, 2), dtype=np.float32))},
            coords={
                "time": [pd.Timestamp("2016-01-01")],
                "latitude": [-15.0, 45.0],
                "longitude": [45.0, 120.0],
                "reftime": pd.Timestamp("2015-12-31T18:00"),
            },
        )
        result = _sample_field(dataset, "field")
        self.assertNotIn("reftime", result.coords)

    def test_pressure_fields_use_canonical_dimension_order(self) -> None:
        dataset = xr.Dataset(
            {
                "u": (
                    ("level", "time", "latitude", "longitude"),
                    np.zeros((3, 2, 2, 2), dtype=np.float32),
                )
            },
            coords={
                "level": [850.0, 700.0, 500.0],
                "time": pd.date_range("2016-01-01", periods=2, freq="3h"),
                "latitude": [10.0, 11.0],
                "longitude": [80.0, 81.0],
            },
        )
        result = _canonical_pressure_dimensions(dataset)
        self.assertEqual(result["u"].dims, ("time", "level", "latitude", "longitude"))

    def test_ncss_url_preserves_repeated_variables(self) -> None:
        url = ncss_url(
            "https://example.invalid/grid",
            ("u-component", "v-component"),
            pd.Timestamp("2016-07-01"),
            pd.Timestamp("2016-08-01"),
            level=850.0,
        )
        self.assertIn("var=u-component&var=v-component", url)
        self.assertIn("vertCoord=850", url)
        self.assertIn("accept=netcdf3", url)

    def test_precipitation_url_uses_ncss_field_name(self) -> None:
        url = ncss_url(
            PRECIPITATION_DATASET,
            (PRECIPITATION_VARIABLE,),
            pd.Timestamp("2016-07-01"),
            pd.Timestamp("2016-07-31T21:00"),
        )
        self.assertIn("Total_precipitation_surface_Average", url)

    def test_precipitation_bounds_expand_to_hourly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            month = "201601"
            path = raw_paths(root, month)["precipitation"]
            path.parent.mkdir(parents=True)
            bounds = pd.date_range("2016-01-01", "2016-02-01", freq="3h")
            midpoints = bounds[:-1] + pd.Timedelta(minutes=90)
            values = np.full((len(midpoints), 2, 2), 24.0, dtype=np.float32)
            dataset = xr.Dataset(
                {
                    PRECIPITATION_VARIABLE: (("time", "latitude", "longitude"), values),
                    "time_bounds": (("time", "bounds_dim"), np.column_stack((bounds[:-1].values, bounds[1:].values))),
                },
                coords={"time": midpoints, "latitude": [-15.0, 45.0], "longitude": [45.0, 120.0]},
            )
            dataset.to_netcdf(path)
            result = precipitation_hourly(root, month)
            self.assertEqual(result.sizes["time"], 31 * 24)
            self.assertEqual(pd.Timestamp(result.time.values[0]), pd.Timestamp("2016-01-01T00:00"))
            self.assertTrue(np.isclose(float(result.isel(time=0, latitude=0, longitude=0)), 24.0 / 86400.0))


if __name__ == "__main__":
    unittest.main()
