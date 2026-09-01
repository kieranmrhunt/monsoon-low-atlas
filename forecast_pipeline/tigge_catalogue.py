#!/usr/bin/env python3
"""Machine-readable TIGGE centre/cycle availability from the ECDS catalogue."""

from __future__ import annotations

import json
import os
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any, Iterable

from .sources import TIGGE_CENTRES


CATALOGUE_URL = "https://ecds.ecmwf.int/api/catalogue/v1/collections/tigge-forecasts"
USER_AGENT = "monsoon-low-atlas-forecast/1.0 (+https://kieranmrhunt.github.io/monsoon-low-atlas/)"

# Precipitation is deliberately not part of the availability gate. It is an
# optional detector-score component, and TIGGE contributors occasionally omit
# t+0 or isolated accumulated-precipitation frames. The adapter marks affected
# accumulation intervals missing so the frozen score renormalises its optional
# weights while still requiring a complete dynamical axis.
REQUIRED_FIELDS = (
    ("u_component_of_wind", "pressure", "500_hpa"),
    ("u_component_of_wind", "pressure", "700_hpa"),
    ("u_component_of_wind", "pressure", "850_hpa"),
    ("v_component_of_wind", "pressure", "500_hpa"),
    ("v_component_of_wind", "pressure", "700_hpa"),
    ("v_component_of_wind", "pressure", "850_hpa"),
    ("mean_sea_level_pressure", "single_level", None),
    ("10_m_u_component_of_wind", "single_level", None),
    ("10_m_v_component_of_wind", "single_level", None),
)
FORECAST_TYPE_NAMES = {
    "cf": "control_forecast",
    "pf": "perturbed_forecast",
    "fc": "high_resolution_forecast",
}


def _read_json_url(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def load_constraints(path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a pinned file or discover the current ECDS constraint resource."""

    if path is not None:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"TIGGE constraints in {path} are not a list")
        return rows, {"url": str(path), "catalogue_updated": None}

    catalogue_url = os.environ.get("LPS_TIGGE_CATALOGUE_URL", CATALOGUE_URL)
    catalogue = _read_json_url(catalogue_url)
    link = next(
        (item for item in catalogue.get("links", []) if item.get("rel") == "constraints"),
        None,
    )
    if not link or not link.get("href"):
        raise RuntimeError("ECDS TIGGE catalogue has no constraints link")
    rows = _read_json_url(str(link["href"]))
    if not isinstance(rows, list):
        raise ValueError("ECDS TIGGE constraints response is not a list")
    return rows, {
        "url": str(link["href"]),
        "catalogue_url": catalogue_url,
        "catalogue_updated": catalogue.get("updated"),
    }


def _matches_cycle(row: dict[str, Any], origin: str, forecast_type: str, cycle: datetime) -> bool:
    value = cycle.astimezone(UTC)
    return (
        origin in row.get("origin", [])
        and forecast_type in row.get("forecast_type", [])
        and value.strftime("%Y") in row.get("year", [])
        and value.strftime("%m") in row.get("month", [])
        and value.strftime("%d") in row.get("day", [])
        and value.strftime("%H:00") in row.get("time", [])
    )


def _field_steps(
    rows: Iterable[dict[str, Any]],
    variable: str,
    level_type: str,
    level_value: str | None,
) -> set[int]:
    output: set[int] = set()
    for row in rows:
        if variable not in row.get("variable", []):
            continue
        if level_type not in row.get("level_type", []):
            continue
        if level_value is not None and level_value not in row.get("level_value", []):
            continue
        output.update(int(value) for value in row.get("leadtime_hour", []))
    return output


class TiggeAvailability:
    """Indexed view of the ECDS constraints for many cycle lookups."""

    def __init__(self, constraints: Iterable[dict[str, Any]]):
        self._index: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in constraints:
            dimensions = (
                row.get("origin", []),
                row.get("forecast_type", []),
                row.get("year", []),
                row.get("month", []),
                row.get("day", []),
                row.get("time", []),
            )
            if any(not values for values in dimensions):
                continue
            for key in product(*dimensions):
                self._index[key].append(row)

    def available_steps(self, model: str, cycle: datetime) -> list[int]:
        """Return the complete common six-hourly dynamical axis for one cycle."""

        if model not in TIGGE_CENTRES:
            raise ValueError(f"Unknown TIGGE model {model!r}")
        centre = TIGGE_CENTRES[model]
        if cycle.tzinfo is None:
            cycle = cycle.replace(tzinfo=UTC)
        else:
            cycle = cycle.astimezone(UTC)
        if cycle < centre.archive_start or cycle.hour not in {0, 12}:
            return []

        axes: list[set[int]] = []
        date_key = (
            cycle.strftime("%Y"),
            cycle.strftime("%m"),
            cycle.strftime("%d"),
            cycle.strftime("%H:00"),
        )
        for short_type in centre.forecast_types:
            long_type = FORECAST_TYPE_NAMES[short_type]
            matching = self._index.get(
                (centre.catalogue_origin, long_type, *date_key),
                [],
            )
            if not matching:
                return []
            for field in REQUIRED_FIELDS:
                steps = _field_steps(matching, *field)
                if not steps:
                    return []
                axes.append(steps)

        common = set.intersection(*axes) if axes else set()
        common = {
            step for step in common
            if 0 <= step <= centre.maximum_horizon_hours and step % 6 == 0
        }
        if not common:
            return []
        first = min(common)
        output: list[int] = []
        for step in range(first, centre.maximum_horizon_hours + 1, 6):
            if step not in common:
                break
            output.append(step)
        return output if output and output[-1] - output[0] >= 24 else []


def available_steps(
    constraints: Iterable[dict[str, Any]],
    model: str,
    cycle: datetime,
) -> list[int]:
    """Convenience wrapper for one-off/tests; batch planners reuse the index."""

    return TiggeAvailability(constraints).available_steps(model, cycle)
