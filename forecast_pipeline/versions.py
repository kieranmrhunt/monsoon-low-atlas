"""Documented operational model generations for forecast-cycle labelling.

The atlas reports the forecast system that was operational at each cycle.  It
does not infer a patch version from GRIB metadata, because the public feeds do
not expose that consistently.  NOAA labels below therefore use an exact
version where an implementation notice supports one and a major-version
"family" where later operational patches share the same forecast system.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=UTC)


VERSION_INTERVALS: dict[str, tuple[dict[str, Any], ...]] = {
    "gfs": (
        {
            "from": "2022112912",
            "label": "GFS v16.3 family",
            "source_url": "https://www.weather.gov/media/notification/pdf2/scn22-104_gfs.v16.3.0_aaa.pdf",
        },
        {
            "from": "2021032212",
            "label": "GFS v16 family",
            "source_url": "https://www.weather.gov/media/notification/pdf2/scn21-20gfs_v16.0_aac.pdf",
        },
        {
            "from": "2019061212",
            "label": "GFS v15 family",
            "source_url": "https://www.weather.gov/media/notification/scn19-40gfs_v15_1.pdf",
        },
        {
            "from": "2017071912",
            "label": "GFS v14",
            "source_url": "https://www.weather.gov/media/notification/pdfs/scn17-67gfs_upgrade_aad.pdf",
        },
        {
            "from": "2015011412",
            "label": "GFS 2015 operational configuration",
            "source_url": "https://www.weather.gov/media/notification/pdfs/scn14-111gfs_upgrade.pdf",
        },
    ),
    "gefs": (
        {
            "from": "2026061512",
            "label": "GEFS v12.3.20",
            "source_url": "https://www.weather.gov/media/notification/pdf_2026/scn26-57_GEFS_Shortwave_radiation_fix.pdf",
        },
        {
            "from": "2020092312",
            "label": "GEFS v12 family",
            "source_url": "https://www.weather.gov/media/notification/pdf2/scn20-75gefs_v12_changes.pdf",
        },
        {
            "from": "2017010100",
            "label": "GEFS v11.3",
            "source_url": "https://www.weather.gov/media/notification/pdf2/scn20-75gefs_v12_changes.pdf",
        },
    ),
    "ifs": (
        {
            "from": "2026051206",
            "label": "IFS Cycle 50r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/567162191/Implementation%2Bof%2BIFS%2BCycle%2B50r1",
        },
    ),
    "ifs-ens": (
        {
            "from": "2026051206",
            "label": "IFS Cycle 50r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/567162191/Implementation%2Bof%2BIFS%2BCycle%2B50r1",
        },
    ),
    "aifs": (
        {
            "from": "2026051206",
            "label": "AIFS Single v2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/567162191/Implementation%2Bof%2BIFS%2BCycle%2B50r1",
        },
    ),
    "aifs-ens": (
        {
            "from": "2026051206",
            "label": "AIFS ENS v2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/620418893/Implementation%2Bof%2BAIFS%2BENS%2Bv2",
        },
    ),
}

# The archive-only GEFS control product shares the operational GEFS model
# generation and its documented implementation dates.
VERSION_INTERVALS["gefs-control"] = VERSION_INTERVALS["gefs"]


def model_version(model: str, cycle: datetime) -> dict[str, Any]:
    """Return a transparent cycle-to-version crosswalk record."""

    if cycle.tzinfo is None:
        cycle = cycle.replace(tzinfo=UTC)
    else:
        cycle = cycle.astimezone(UTC)
    for record in VERSION_INTERVALS.get(model, ()):
        valid_from = _stamp(str(record["from"]))
        if cycle >= valid_from:
            return {
                "label": record["label"],
                "valid_from_utc": valid_from.isoformat().replace("+00:00", "Z"),
                "source_url": record["source_url"],
                "basis": "documented operational implementation interval",
            }
    return {
        "label": "Version not yet crosswalked",
        "valid_from_utc": None,
        "source_url": None,
        "basis": "no documented interval in the atlas crosswalk",
    }
