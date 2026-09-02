from __future__ import annotations

import unittest

import numpy as np

from .common import EARTH_RADIUS_M, relative_vorticity_x1e5


class RelativeVorticityTest(unittest.TestCase):
    def test_solid_body_rotation(self) -> None:
        latitudes = np.arange(-45.0, 46.0, 1.0)
        longitudes = np.arange(0.0, 91.0, 1.0)
        angular_velocity = 7.2921159e-5
        u = angular_velocity * EARTH_RADIUS_M * np.cos(np.deg2rad(latitudes))[:, None]
        u = np.broadcast_to(u, (len(latitudes), len(longitudes)))
        v = np.zeros_like(u)
        actual = relative_vorticity_x1e5(
            u,
            v,
            latitudes=latitudes,
            longitudes=longitudes,
        )
        expected = 2.0 * angular_velocity * np.sin(np.deg2rad(latitudes)) * 1.0e5
        wanted = np.broadcast_to(expected[2:-2, None], actual[2:-2, 2:-2].shape)
        np.testing.assert_allclose(actual[2:-2, 2:-2], wanted, atol=0.005)


if __name__ == "__main__":
    unittest.main()
