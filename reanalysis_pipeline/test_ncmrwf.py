from __future__ import annotations

import unittest

from reanalysis_pipeline.ncmrwf import month_days, request_key, request_payload


class NCMRWFRequestTests(unittest.TestCase):
    def test_pressure_payload_matches_portal_normalisation(self) -> None:
        payload = request_payload("pressure", 2016, 7, days=("01",))
        self.assertEqual(payload["dataset_type"], "prl")
        self.assertEqual(payload["pressure_level"], ["850_hpa", "700_hpa", "500_hpa"])
        self.assertEqual(payload["time"], ["00", "03", "06", "09", "12", "15", "18", "21"])
        self.assertEqual(payload["area"], {"north": 45, "south": -15, "east": 120, "west": 45})

    def test_surface_payload_has_only_tracking_fields(self) -> None:
        payload = request_payload("surface", 2016, 7, days=("01",))
        self.assertEqual(payload["dataset_type"], "2df")
        self.assertEqual(
            payload["variables"],
            ["UGRD-10m", "VGRD-10m", "PRMSL-msl", "APCP-sfc"],
        )

    def test_calendar_and_key_are_deterministic(self) -> None:
        self.assertEqual(len(month_days(2020, 2)), 29)
        self.assertEqual(request_key("surface", 2016, 7, ("01",)), "imdaa-surface-201607-01")

    def test_invalid_day_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            request_payload("pressure", 2019, 2, days=("29",))


if __name__ == "__main__":
    unittest.main()
