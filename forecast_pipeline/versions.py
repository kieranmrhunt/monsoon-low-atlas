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
    "ukmo-global": (
        {
            "from": "2016031900",
            "label": "Met Office Global 17 km archive product",
            "source_url": "https://catalogue.ceda.ac.uk/uuid/86df725b793b4b4cb0ca0646686bd783",
            "basis": "BADC product family; the exact operational suite is not encoded consistently in these GRIB files",
        },
    ),
    "mogreps-g": (
        {
            "from": "2024010100",
            "label": "MOGREPS-G operational ensemble",
            "source_url": "https://registry.opendata.aws/met-office-global-ensemble/",
            "basis": (
                "Met Office AWS Open Data product family; the exact operational suite "
                "is not encoded as a stable cycle-level version in the public objects"
            ),
        },
    ),
    "tigge-ecmwf": (
        {
            "from": "2021101212",
            "label": "IFS Cycle 47r3",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/226510127/Implementation+of+IFS+Cycle+47r3",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2021051112",
            "label": "IFS Cycle 47r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/197704845/Implementation+of+IFS+Cycle+47r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2020063012",
            "label": "IFS Cycle 47r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/179736603/Implementation+of+IFS+Cycle+47r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2019061112",
            "label": "IFS Cycle 46r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/131380806/Implementation+of+IFS+cycle+46r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2018060512",
            "label": "IFS Cycle 45r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/97385120/Implementation+of+IFS+cycle+45r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2017071112",
            "label": "IFS Cycle 43r3",
            "source_url": "https://confluence.ecmwf.int/display/FCST/Implementation+of+IFS+cycle+43r3",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2016112212",
            "label": "IFS Cycle 43r1",
            "source_url": "https://confluence.ecmwf.int/spaces/TIGGE/pages/53523308/Model+upgrades",
            "basis": "ECMWF TIGGE model-upgrade history",
        },
        {
            "from": "2016030812",
            "label": "IFS Cycle 41r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/51726000/Detailed+information+of+implementation+of+IFS+cycle+41r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2015051212",
            "label": "IFS Cycle 41r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/108117957/Implementation+of+IFS+Cycle+41r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2013111912",
            "label": "IFS Cycle 40r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/108117951/Implementation+of+IFS+Cycle+40r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2013062512",
            "label": "IFS Cycle 38r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/108117145/Implementation+of+IFS+Cycle+38r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2012061912",
            "label": "IFS Cycle 38r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/311145712/Implementation+of+IFS+Cycle+38r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2011111512",
            "label": "IFS Cycle 37r3",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/108117147/Implementation+of+IFS+Cycle+37r3",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2011051812",
            "label": "IFS Cycle 37r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/108117137/Implementation+of+IFS+Cycle+37r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2010110912",
            "label": "IFS Cycle 36r4",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237805/Implementation+of+IFS+Cycle+36r4",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2010062212",
            "label": "IFS Cycle 36r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/108117185/Implementation+of+IFS+Cycle+36r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2010012612",
            "label": "IFS Cycle 36r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237640/Implementation+of+IFS+Cycle+36r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2009090812",
            "label": "IFS Cycle 35r3",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237802/Implementation+of+IFS+Cycle+35r3",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2009031012",
            "label": "IFS Cycle 35r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237637/Implementation+of+IFS+Cycle+35r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2008093012",
            "label": "IFS Cycle 35r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237743/Implementation+of+IFS+Cycle+35r1+33r2",
            "basis": "documented operational implementation interval; the legacy page title also contains 33r2",
        },
        {
            "from": "2008060312",
            "label": "IFS Cycle 33r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237636/Implementation+of+IFS+Cycle+33r1",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2008031112",
            "label": "IFS Cycle 32r3V",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237838/Implementation+of+IFS+Cycle+32r3V",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2007110612",
            "label": "IFS Cycle 32r3",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237830/Implementation+of+IFS+Cycle+32r3",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2007060512",
            "label": "IFS Cycle 32r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237740/Implementation+of+IFS+Cycle+32r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2006121212",
            "label": "IFS Cycle 31r2",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237632/Implementation+of+IFS+Cycle+31r2",
            "basis": "documented operational implementation interval",
        },
        {
            "from": "2006091212",
            "label": "IFS Cycle 31r1",
            "source_url": "https://confluence.ecmwf.int/spaces/FCST/pages/304237799/Implementation+of+IFS+Cycle+31r1",
            "basis": "documented operational implementation interval",
        },
    ),
}

# The archive-only GEFS control product shares the operational GEFS model
# generation and its documented implementation dates.
VERSION_INTERVALS["gefs-control"] = VERSION_INTERVALS["gefs"]

# TIGGE's participating centres expose long model-upgrade tables, but several
# do not provide a stable machine-readable cycle-to-suite crosswalk. Keep the
# archive/model family explicit instead of inventing a patch version. ECMWF is
# the exception above because its IFS implementation boundaries are documented
# precisely enough for a cycle-level crosswalk.
_TIGGE_CENTRE_FAMILIES = {
    "tigge-bom": ("2007010100", "BoM TIGGE operational ensemble"),
    "tigge-cma": ("2007010100", "CMA TIGGE operational ensemble"),
    "tigge-cptec": ("2008010100", "CPTEC TIGGE operational ensemble"),
    "tigge-dwd": ("2020120100", "DWD TIGGE operational ensemble"),
    "tigge-eccc": ("2007010100", "ECCC TIGGE operational ensemble"),
    "tigge-imd": ("2020070100", "IMD TIGGE operational ensemble"),
    "tigge-jma": ("2006100100", "JMA TIGGE operational ensemble"),
    "tigge-kma": ("2007010100", "KMA TIGGE operational ensemble"),
    "tigge-mf": ("2007010100", "Météo-France TIGGE operational ensemble"),
    "tigge-ncep": ("2007010100", "NCEP TIGGE operational ensemble"),
    "tigge-ncmrwf": ("2017080100", "NCMRWF TIGGE operational ensemble"),
    "tigge-ukmo": ("2006100100", "UKMO TIGGE operational ensemble"),
}
for _model, (_start, _label) in _TIGGE_CENTRE_FAMILIES.items():
    VERSION_INTERVALS[_model] = ({
        "from": _start,
        "label": _label,
        "source_url": "https://confluence.ecmwf.int/spaces/TIGGE/pages/40109876/Models",
        "basis": (
            "TIGGE centre/model family; the exact operational configuration "
            "varies within the archive and is not assigned without a documented cycle crosswalk"
        ),
    },)


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
                "basis": record.get("basis", "documented operational implementation interval"),
            }
    return {
        "label": "Version not yet crosswalked",
        "valid_from_utc": None,
        "source_url": None,
        "basis": "no documented interval in the atlas crosswalk",
    }
