#!/usr/bin/env python3
"""Official forecast-source adapters used by the operational atlas updater."""

from __future__ import annotations

import json
import io
import logging
import math
import os
import random
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, timedelta, datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from eccodes import codes_get, codes_get_message, codes_grib_new_from_file, codes_release

from .forecast_core import (
    GridField,
    GRID_LATS,
    GRID_LONS,
    assign_systems,
    candidate_cycles,
    compact_weather,
    cycle_id,
    decode_grib_message,
    decode_grib_messages,
    grid_metadata,
    iso_z,
    parse_cycle,
    relative_vorticity_x1e5,
    to_mslp_hpa,
    to_precip_mm,
    trailing_24h,
    utc_now,
    validate_cycle_payload,
)
from .v56_tracking import parameter_sha256, track_forecast_member
from .versions import model_version


LOGGER = logging.getLogger("mla.forecast.sources")
USER_AGENT = "monsoon-low-atlas-forecast/1.0 (+https://kieranmrhunt.github.io/monsoon-low-atlas/)"

NOAA_GEFS_ARCHIVE_START = datetime(2017, 1, 1, 0, tzinfo=UTC)
WEATHERBENCH_HRES_START = datetime(2016, 1, 1, 0, tzinfo=UTC)
WEATHERBENCH_HRES_END = datetime(2022, 12, 31, 12, tzinfo=UTC)
WEATHERBENCH_IFS_ENS_START = datetime(2018, 1, 1, 0, tzinfo=UTC)
WEATHERBENCH_IFS_ENS_END = datetime(2022, 12, 31, 12, tzinfo=UTC)


def tigge_archive_provider(model: str, cycle: datetime) -> str:
    """Name the retrieval service actually used for a TIGGE model-cycle."""

    value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    if model == "tigge-ncep" and value >= NOAA_GEFS_ARCHIVE_START:
        return "NOAA Open Data GEFS archive"
    if (
        model == "tigge-ecmwf"
        and WEATHERBENCH_IFS_ENS_START <= value <= WEATHERBENCH_IFS_ENS_END
    ):
        return "WeatherBench 2 public IFS ENS archive"
    return "ECMWF ECDS TIGGE archive"


def available_forecast_steps(model: str, cycle: datetime) -> list[int]:
    """Return every six-hourly lead supplied by the selected forecast stream."""

    value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
    if model in {"gfs", "gefs", "gefs-control", "aigfs", "aigefs"}:
        horizon = 384
    elif model == "graphcast-noaa":
        horizon = 240
    elif model in {"ifs", "ifs-ens"}:
        horizon = 360 if value.hour in {0, 12} else 144
    elif model in {"aifs", "aifs-ens"}:
        horizon = 360
    elif model in TIGGE_CENTRES:
        horizon = TIGGE_CENTRES[model].maximum_horizon_hours
    elif model == "ukmo-global":
        horizon = 144
    elif model == "mogreps-g":
        horizon = 246
    else:
        raise ValueError(f"No available-lead policy is defined for {model}")
    return list(range(0, horizon + 1, 6))


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    label: str
    centre: str
    kind: str
    expected_members: int
    description: str
    source_url: str
    source_name: str
    licence: str
    colour: str


@dataclass(frozen=True)
class TiggeCentre:
    """Centre-specific request details for the common ECDS TIGGE adapter."""

    model_id: str
    archive_origin: str
    catalogue_origin: str
    archive_start: datetime
    maximum_horizon_hours: int
    forecast_types: tuple[str, ...] = ("cf", "pf")


TIGGE_CENTRES: dict[str, TiggeCentre] = {
    "tigge-bom": TiggeCentre("tigge-bom", "ammc", "bom", datetime(2007, 1, 1, tzinfo=UTC), 246),
    "tigge-cma": TiggeCentre("tigge-cma", "babj", "cma", datetime(2007, 1, 1, tzinfo=UTC), 360),
    "tigge-cptec": TiggeCentre("tigge-cptec", "sbsj", "cptec", datetime(2008, 1, 1, tzinfo=UTC), 360),
    # DWD has no control-forecast concept in TIGGE; its perturbed ensemble is
    # sufficient for the model-neutral detector and linker.
    "tigge-dwd": TiggeCentre("tigge-dwd", "edzw", "dwd", datetime(2020, 12, 1, tzinfo=UTC), 180, ("pf",)),
    "tigge-eccc": TiggeCentre("tigge-eccc", "cwao", "eccc", datetime(2007, 1, 1, tzinfo=UTC), 384),
    "tigge-ecmwf": TiggeCentre("tigge-ecmwf", "ecmf", "ecmwf", datetime(2006, 10, 1, tzinfo=UTC), 360),
    "tigge-imd": TiggeCentre("tigge-imd", "vabb", "imd", datetime(2020, 7, 1, tzinfo=UTC), 240),
    "tigge-jma": TiggeCentre("tigge-jma", "rjtd", "jma", datetime(2006, 10, 1, tzinfo=UTC), 264),
    "tigge-kma": TiggeCentre("tigge-kma", "rksl", "kma", datetime(2007, 1, 1, tzinfo=UTC), 288),
    "tigge-mf": TiggeCentre("tigge-mf", "lfpw", "mf", datetime(2007, 1, 1, tzinfo=UTC), 108),
    "tigge-ncep": TiggeCentre("tigge-ncep", "kwbc", "ncep", datetime(2007, 1, 1, tzinfo=UTC), 384),
    "tigge-ncmrwf": TiggeCentre("tigge-ncmrwf", "dems", "ncmrwf", datetime(2017, 8, 1, tzinfo=UTC), 240),
    "tigge-ukmo": TiggeCentre("tigge-ukmo", "egrr", "ukmo", datetime(2006, 10, 1, tzinfo=UTC), 360),
}

TIGGE_MODEL_IDS = tuple(TIGGE_CENTRES)


MODEL_DEFINITIONS: dict[str, ModelDefinition] = {
    "gfs": ModelDefinition(
        "gfs", "GFS", "NOAA/NCEP", "deterministic", 1,
        "NOAA Global Forecast System deterministic forecast",
        "https://registry.opendata.aws/noaa-gfs-bdp-pds/", "NOAA Open Data cloud mirror", "NOAA public data", "#d14b39",
    ),
    "gefs": ModelDefinition(
        "gefs", "GEFS", "NOAA/NCEP", "ensemble", 31,
        "NOAA Global Ensemble Forecast System control plus 30 perturbed members",
        "https://registry.opendata.aws/noaa-gefs/", "NOAA Open Data cloud mirror", "NOAA public data", "#e8872f",
    ),
    "gefs-control": ModelDefinition(
        "gefs-control", "GEFS control", "NOAA/NCEP", "deterministic", 1,
        "NOAA Global Ensemble Forecast System unperturbed control member, retained for pre-GFS-cloud historical coverage",
        "https://registry.opendata.aws/noaa-gefs/", "NOAA Open Data cloud mirror", "NOAA public data", "#b46722",
    ),
    "aigfs": ModelDefinition(
        "aigfs", "AIGFS", "NOAA/NCEP", "deterministic", 1,
        "NOAA Artificial Intelligence Global Forecast System deterministic forecast",
        "https://www.nco.ncep.noaa.gov/pmb/products/aigfs/", "NOAA NOMADS", "NOAA public data", "#00a6a6",
    ),
    "aigefs": ModelDefinition(
        "aigefs", "AIGEFS", "NOAA/NCEP", "ensemble", 31,
        "NOAA Artificial Intelligence Global Ensemble Forecast System, 31 independently trained members",
        "https://www.nco.ncep.noaa.gov/pmb/products/aigefs/", "NOAA NOMADS", "NOAA public data", "#c51b8a",
    ),
    "graphcast-noaa": ModelDefinition(
        "graphcast-noaa", "GraphCast", "NOAA/CIRA", "deterministic", 1,
        "GraphCast Operational reforecasts initialized from NOAA GFS analyses",
        "https://registry.opendata.aws/aiwp/", "NOAA/CIRA AIWP archive", "NOAA public data", "#7a3db8",
    ),
    "ifs": ModelDefinition(
        "ifs", "IFS", "ECMWF", "deterministic", 1,
        "ECMWF Integrated Forecasting System deterministic forecast",
        "https://data.ecmwf.int/forecasts/", "ECMWF Open Data", "CC BY 4.0", "#285f9d",
    ),
    "ifs-ens": ModelDefinition(
        "ifs-ens", "IFS ENS", "ECMWF", "ensemble", 50,
        "ECMWF IFS 50-member perturbed ensemble; the operational deterministic IFS is shown separately",
        "https://data.ecmwf.int/forecasts/", "ECMWF Open Data", "CC BY 4.0", "#4d79b8",
    ),
    "aifs": ModelDefinition(
        "aifs", "AIFS Single", "ECMWF", "deterministic", 1,
        "ECMWF Artificial Intelligence Forecasting System deterministic forecast",
        "https://data.ecmwf.int/forecasts/", "ECMWF Open Data", "CC BY 4.0", "#6d3e91",
    ),
    "aifs-ens": ModelDefinition(
        "aifs-ens", "AIFS ENS", "ECMWF", "ensemble", 51,
        "ECMWF Artificial Intelligence Forecasting System control plus 50 perturbed members",
        "https://data.ecmwf.int/forecasts/", "ECMWF Open Data", "CC BY 4.0", "#9a54ad",
    ),
    "ukmo-global": ModelDefinition(
        "ukmo-global", "Met Office Global", "Met Office", "deterministic", 1,
        "Archived Met Office operational global deterministic forecast",
        "https://catalogue.ceda.ac.uk/uuid/86df725b793b4b4cb0ca0646686bd783",
        "CEDA/BADC Met Office Global archive", "CC BY-NC-SA 4.0", "#007e88",
    ),
    "mogreps-g": ModelDefinition(
        "mogreps-g", "MOGREPS-G", "Met Office", "ensemble", 18,
        "Met Office Global and Regional Ensemble Prediction System global ensemble",
        "https://registry.opendata.aws/met-office-global-ensemble/",
        "Met Office AWS Open Data", "CC BY-SA 4.0", "#00a7a5",
    ),
    "tigge-ecmwf": ModelDefinition(
        "tigge-ecmwf", "ECMWF TIGGE ENS", "ECMWF", "ensemble", 51,
        "Historical ECMWF control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#73539b",
    ),
    "tigge-bom": ModelDefinition(
        "tigge-bom", "BoM TIGGE ENS", "Australian Bureau of Meteorology", "ensemble", 17,
        "Historical BoM control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#d55e00",
    ),
    "tigge-cma": ModelDefinition(
        "tigge-cma", "CMA TIGGE ENS", "China Meteorological Administration", "ensemble", 30,
        "Historical CMA control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#e69f00",
    ),
    "tigge-cptec": ModelDefinition(
        "tigge-cptec", "CPTEC TIGGE ENS", "CPTEC", "ensemble", 15,
        "Historical CPTEC control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#a65628",
    ),
    "tigge-dwd": ModelDefinition(
        "tigge-dwd", "DWD TIGGE ENS", "Deutscher Wetterdienst", "ensemble", 40,
        "Historical DWD perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#009e73",
    ),
    "tigge-eccc": ModelDefinition(
        "tigge-eccc", "ECCC TIGGE ENS", "Environment and Climate Change Canada", "ensemble", 21,
        "Historical ECCC control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#56b4e9",
    ),
    "tigge-imd": ModelDefinition(
        "tigge-imd", "IMD TIGGE ENS", "India Meteorological Department", "ensemble", 21,
        "Historical IMD control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#cc79a7",
    ),
    "tigge-jma": ModelDefinition(
        "tigge-jma", "JMA TIGGE ENS", "Japan Meteorological Agency", "ensemble", 51,
        "Historical JMA control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#0072b2",
    ),
    "tigge-kma": ModelDefinition(
        "tigge-kma", "KMA TIGGE ENS", "Korea Meteorological Administration", "ensemble", 26,
        "Historical KMA control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#00a087",
    ),
    "tigge-mf": ModelDefinition(
        "tigge-mf", "Météo-France TIGGE ENS", "Météo-France", "ensemble", 35,
        "Historical Météo-France control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#8c6d00",
    ),
    "tigge-ncep": ModelDefinition(
        "tigge-ncep", "NCEP TIGGE ENS", "NOAA/NCEP", "ensemble", 31,
        "Historical NCEP control plus perturbed ensemble from NOAA Open Data (2017 onward) and ECDS TIGGE (earlier)",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#332288",
    ),
    "tigge-ncmrwf": ModelDefinition(
        "tigge-ncmrwf", "NCMRWF TIGGE ENS", "National Centre for Medium Range Weather Forecasting", "ensemble", 12,
        "Historical NCMRWF control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY-NC 4.0", "#882255",
    ),
    "tigge-ukmo": ModelDefinition(
        "tigge-ukmo", "UKMO TIGGE ENS", "Met Office", "ensemble", 18,
        "Historical Met Office control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#44aa99",
    ),
}

DEFAULT_MODELS = (
    "gfs", "gefs", "aigfs", "aigefs", "graphcast-noaa", "mogreps-g",
    "ifs", "ifs-ens", "aifs", "aifs-ens",
)


class DownloadError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, timeout: int = 90, retries: int = 4):
        self.timeout = timeout
        self.retries = retries

    def get(self, url: str, *, byte_range: tuple[int, int | None] | None = None) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if byte_range is not None:
            end = "" if byte_range[1] is None else str(byte_range[1])
            headers["Range"] = f"bytes={byte_range[0]}-{end}"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
                    status = getattr(response, "status", response.getcode())
                if byte_range is not None:
                    expected = (
                        None
                        if byte_range[1] is None
                        else byte_range[1] - byte_range[0] + 1
                    )
                    if status != 206 and (expected is None or len(payload) != expected):
                        raise DownloadError(f"Server ignored byte range for {url} (HTTP {status}, {len(payload)} bytes)")
                    if expected is not None and len(payload) != expected:
                        raise DownloadError(f"Truncated byte range from {url}: expected {expected}, got {len(payload)}")
                return payload
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, DownloadError) as error:
                last_error = error
                if isinstance(error, urllib.error.HTTPError) and error.code in {400, 401, 403, 404}:
                    break
                if attempt + 1 < self.retries:
                    time.sleep(min(2 ** attempt, 8))
        raise DownloadError(f"Could not download {url}: {last_error}") from last_error

    def text(self, url: str) -> str:
        return self.get(url).decode("utf-8")

    def exists(self, url: str) -> bool:
        try:
            self.get(url, byte_range=(0, 0))
            return True
        except DownloadError:
            return False


@dataclass(frozen=True)
class IndexRecord:
    offset: int
    length: int
    description: str
    attributes: dict[str, str]


def parse_ncep_index(text: str, object_size: int | None = None) -> list[IndexRecord]:
    raw: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = re.match(r"^\d+:(\d+):(.*)$", line.strip())
        if match:
            raw.append((int(match.group(1)), match.group(2)))
    records: list[IndexRecord] = []
    for index, (offset, description) in enumerate(raw):
        next_offset = raw[index + 1][0] if index + 1 < len(raw) else object_size
        if next_offset is None:
            # Last messages are never selected by this pipeline, but retaining a
            # sentinel makes the inventory parser independently testable.
            length = -1
        else:
            length = int(next_offset) - offset
        records.append(IndexRecord(offset, length, description, {}))
    return records


def parse_ecmwf_index(text: str) -> list[IndexRecord]:
    records: list[IndexRecord] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        attributes = {key: str(value) for key, value in row.items() if not key.startswith("_")}
        records.append(IndexRecord(int(row["_offset"]), int(row["_length"]), line, attributes))
    return records


def _fetch_record(client: HttpClient, url: str, record: IndexRecord) -> bytes:
    if record.length <= 0:
        # NCEP inventories omit the object size, so the final GRIB message has no
        # calculable end byte. HTTP's open-ended range syntax retrieves it safely.
        return client.get(url, byte_range=(record.offset, None))
    return client.get(url, byte_range=(record.offset, record.offset + record.length - 1))


def _ncep_record(records: Sequence[IndexRecord], token: str) -> IndexRecord:
    matches = [record for record in records if token in record.description]
    if not matches:
        raise DownloadError(f"NCEP inventory lacks {token}")
    return matches[0]


def _ncep_precip_record(records: Sequence[IndexRecord], step: int) -> IndexRecord | None:
    matches = [record for record in records if ":APCP:surface:" in record.description]
    if not matches or step == 0:
        return None
    exact = [
        record for record in matches
        if f":0-{step} hour acc" in record.description
        or (step % 24 == 0 and f":0-{step // 24} day acc" in record.description)
    ]
    if exact:
        return exact[-1]
    # Prefer the shortest ending-at-step interval; it can be accumulated safely.
    ending: list[tuple[int, IndexRecord]] = []
    for record in matches:
        match = re.search(r":(\d+)-(\d+) hour acc", record.description)
        if match and int(match.group(2)) == step:
            ending.append((int(match.group(2)) - int(match.group(1)), record))
    if ending:
        return min(ending, key=lambda item: item[0])[1]
    return matches[0]


def _precip_is_cumulative(field_step_range: str, step: int) -> bool:
    if step == 0:
        return True
    value = str(field_step_range).strip()
    if value == str(step):
        return True
    match = re.match(r"^(\d+)-(\d+)$", value)
    return bool(match and int(match.group(1)) == 0 and int(match.group(2)) == step)


def _finalise_precip(fields: list[np.ndarray], cumulative_flags: list[bool]) -> np.ndarray:
    if not fields:
        raise ValueError("No precipitation frames")
    output = np.zeros_like(np.stack(fields), dtype=np.float32)
    for index, field in enumerate(fields):
        if index == 0:
            output[index] = np.maximum(field, 0.0)
        elif cumulative_flags[index]:
            output[index] = np.maximum(field, output[index - 1])
        else:
            output[index] = output[index - 1] + np.maximum(field, 0.0)
    return output


def _interpolate_isolated_native_gaps(
    fields: Sequence[np.ndarray | None],
    steps: Sequence[int],
) -> list[np.ndarray]:
    """Fill isolated missing provider frames between six-hour neighbours.

    NOAA's historical GEFS object store has a small number of member-level
    holes where one six-hour file is absent but both adjacent files and the
    remainder of the forecast are present. Rejecting the whole member would
    turn a one-frame archive defect into a large ensemble-completeness loss.
    Only a single missing native frame bounded by the immediately adjacent
    frames is repairable here; leading, trailing, or consecutive gaps remain
    hard failures.
    """

    values = list(fields)
    native_steps = [int(step) for step in steps]
    if len(values) != len(native_steps):
        raise DownloadError("native field/step lengths differ")
    missing = [index for index, value in enumerate(values) if value is None]
    for index in missing:
        if index == 0 or index + 1 >= len(values):
            raise DownloadError(f"source gap at +{native_steps[index]} h is not bounded")
        previous = values[index - 1]
        following = values[index + 1]
        if previous is None or following is None:
            raise DownloadError(f"source gap at +{native_steps[index]} h is not isolated")
        width = native_steps[index + 1] - native_steps[index - 1]
        if width != 12:
            raise DownloadError(
                f"source gap at +{native_steps[index]} h spans {width} h rather than one native frame"
            )
        weight = np.float32(
            (native_steps[index] - native_steps[index - 1]) / width
        )
        values[index] = (
            np.asarray(previous, dtype=np.float32) * (1.0 - weight)
            + np.asarray(following, dtype=np.float32) * weight
        )
    if any(value is None for value in values):
        raise DownloadError("unresolved native source gap")
    return [np.asarray(value, dtype=np.float32) for value in values]


class BaseAdapter:
    definition: ModelDefinition

    def __init__(self, client: HttpClient | None = None, workers: int = 16):
        self.client = client or HttpClient()
        self.workers = max(1, workers)

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested != "latest":
            value = parse_cycle(requested)
            if not self.cycle_complete(value, horizon):
                raise DownloadError(f"{self.definition.label} cycle {requested} is not complete to +{horizon} h")
            return value
        for value in candidate_cycles(limit=8):
            if self.cycle_complete(value, horizon):
                return value
        raise DownloadError(f"No recent {self.definition.label} cycle is complete to +{horizon} h")

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        raise NotImplementedError

    def resolve_available_cycle(self, requested: str) -> tuple[datetime, list[int]]:
        """Resolve the newest cycle and retain its complete provider lead axis."""

        if requested != "latest":
            value = parse_cycle(requested)
            steps = available_forecast_steps(self.definition.id, value)
            resolved = self.resolve_cycle(requested, int(steps[-1]))
            return resolved, steps
        for value in candidate_cycles(limit=8):
            steps = available_forecast_steps(self.definition.id, value)
            if self.cycle_complete(value, int(steps[-1])):
                return value, steps
        raise DownloadError(f"No recent {self.definition.label} cycle has its complete provider lead axis")

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def _payload(
        self,
        cycle: datetime,
        steps: Sequence[int],
        tracks: list[dict[str, Any]],
        member_ids: list[str],
        vorticity_mean: np.ndarray,
        precipitation_mean_cumulative: np.ndarray,
        warnings: list[str],
        tracking_qa: list[dict[str, Any]],
        expected_members: int | None = None,
    ) -> dict[str, Any]:
        definition = self.definition
        systems = assign_systems(tracks)
        basis = "deterministic" if definition.kind == "deterministic" else f"{len(member_ids)}-member ensemble mean"
        payload: dict[str, Any] = {
            "schema": "mla-forecast-cycle-v1",
            "model": {
                "id": definition.id,
                "label": definition.label,
                "centre": definition.centre,
                "kind": definition.kind,
                "description": definition.description,
                "colour": definition.colour,
            },
            "cycle": cycle_id(cycle),
            "cycle_utc": iso_z(cycle),
            "generated_utc": iso_z(utc_now()),
            "steps": [int(step) for step in steps],
            "valid_times": [iso_z(cycle + timedelta(hours=int(step))) for step in steps],
            "horizon_hours": int(max(steps)),
            "temporal_resolution_hours": 6,
            "provider_maximum_available_lead": [int(step) for step in steps] == available_forecast_steps(definition.id, cycle),
            "grid": grid_metadata(),
            "members": {
                "available": len(member_ids),
                "expected": definition.expected_members if expected_members is None else expected_members,
                "ids": member_ids,
            },
            "tracks": tracks,
            "systems": systems,
            "weather": compact_weather(
                np.maximum(vorticity_mean, 0.0),
                trailing_24h(precipitation_mean_cumulative, steps),
                basis,
            ),
            "source": {
                "provider": definition.centre,
                "service": definition.source_name,
                "url": definition.source_url,
                "licence": definition.licence,
                "retrieval": "provider inventory byte ranges; atlas domain resampled to 1 degree",
            },
            "model_version": model_version(definition.id, cycle),
            "method": {
                "track_status": "provisional forecast guidance; not an official best track or a v5.6 catalogue event",
                "detector": "frozen v5.6 catalogue object detector (v5.4.2 continuity parent)",
                "linker": "frozen v5.6 probabilistic continuity linker and global stitcher",
                "parameter_sha256": parameter_sha256(),
                "signals": [
                    "850/700/500-hPa relative vorticity",
                    "local mean-sea-level pressure deficit",
                    "model precipitation after +24 h",
                    "10-m wind",
                    "850/700/500-hPa steering wind",
                ],
                "native_to_linker_time": "continuous fields linearly interpolated from the complete six-hourly provider output to the hourly linker clock",
                "spatial_derivatives": "relative vorticity is derived from winds after every provider is resampled to the common 1-degree atlas grid",
                "minimum_published_support_hours": 18,
                "forecast_physical_gate": "full frozen v5.6 physical-event gate when the forecast observes a complete 72-hour span, including physical continuity and release-domain support",
                "retrospective_gate_exception": "only tracks touching initialization or the forecast horizon may scale the v5.6 duration requirements; three strong release-domain positions remain mandatory",
                "no_genesis_basin_rule": True,
                "weather_basis": basis,
                "precipitation_window": "trailing 24 hours; since initialization before +24 h",
            },
            "tracking_qa": tracking_qa,
            "warnings": warnings,
        }
        payload["qa"] = validate_cycle_payload(payload)
        if payload["qa"]["status"] == "failed":
            raise ValueError(f"{definition.id} payload failed QA: {payload['qa']['errors']}")
        return payload


@dataclass(frozen=True)
class LocalGribRecord:
    start_step: int
    end_step: int
    payload: bytes


def _validate_local_grib_cycle(path: Path, cycle: datetime) -> None:
    """Reject BADC files whose first GRIB message belongs to another cycle."""

    with path.open("rb") as stream:
        handle = codes_grib_new_from_file(stream)
    if handle is None:
        raise DownloadError(f"No GRIB messages in {path}")
    try:
        data_date = int(codes_get(handle, "dataDate"))
        data_time = int(codes_get(handle, "dataTime"))
    finally:
        codes_release(handle)
    if data_date != int(cycle.strftime("%Y%m%d")) or data_time != int(cycle.strftime("%H%M")):
        raise DownloadError(
            f"BADC header {data_date:08d}{data_time:04d} disagrees with requested "
            f"{cycle:%Y%m%d%H%M} in {path}"
        )


def _read_local_grib(path: Path, cycle: datetime, maximum_step: int) -> list[LocalGribRecord]:
    """Read and validate the useful messages in one BADC multi-message file."""

    records: list[LocalGribRecord] = []
    with path.open("rb") as stream:
        while True:
            handle = codes_grib_new_from_file(stream)
            if handle is None:
                break
            try:
                data_date = int(codes_get(handle, "dataDate"))
                data_time = int(codes_get(handle, "dataTime"))
                start_step = int(codes_get(handle, "startStep"))
                end_step = int(codes_get(handle, "endStep"))
                if data_date != int(cycle.strftime("%Y%m%d")) or data_time != int(cycle.strftime("%H%M")):
                    raise DownloadError(
                        f"BADC header {data_date:08d}{data_time:04d} disagrees with requested {cycle:%Y%m%d%H%M} in {path}"
                    )
                if end_step <= maximum_step:
                    records.append(LocalGribRecord(start_step, end_step, bytes(codes_get_message(handle))))
            finally:
                codes_release(handle)
    if not records:
        raise DownloadError(f"No forecast messages through +{maximum_step} h in {path}")
    return records


class BadcUkmoAdapter(BaseAdapter):
    """Met Office operational global forecasts mounted in the local BADC archive."""

    DEFAULT_ROOT = Path("/badc/ukmo-nwp/data/global-grib")
    AREAS = ("B", "F")
    PRODUCT = "WSGlobal17km"
    FIELD_NAMES = {
        "mslp": "mean_sea_level_pressure",
        "u10": "wind_u_10m",
        "v10": "wind_v_10m",
        "u850": "wind_u_sl_850hPa",
        "v850": "wind_v_sl_850hPa",
        "u700": "wind_u_sl_700hPa",
        "v700": "wind_v_sl_700hPa",
        "u500": "wind_u_sl_500hPa",
        "v500": "wind_v_sl_500hPa",
    }

    def __init__(self, root: str | Path | None = None, workers: int = 1):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["ukmo-global"]
        self.root = Path(root) if root else self.DEFAULT_ROOT

    def _folder(self, cycle: datetime) -> Path:
        return self.root / cycle.strftime("%Y/%m/%d")

    def _field_path(self, cycle: datetime, field_name: str, area: str, horizon: int) -> Path:
        prefix = f"{cycle:%Y%m%d%H}_{self.PRODUCT}_{field_name}_Area{area}_"
        candidates: list[tuple[int, Path]] = []
        for path in self._folder(cycle).glob(f"{prefix}*.grib"):
            match = re.search(r"_(\d{6})\.grib$", path.name)
            if match and int(match.group(1)) >= horizon:
                candidates.append((int(match.group(1)), path))
        if not candidates:
            raise DownloadError(
                f"BADC lacks {field_name} Area{area} for {cycle:%Y%m%d%H} through +{horizon} h"
            )
        return min(candidates, key=lambda item: item[0])[1]

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested == "latest":
            raise DownloadError("The BADC Met Office source is historical and requires an explicit cycle")
        value = parse_cycle(requested)
        if not self.cycle_complete(value, horizon):
            raise DownloadError(f"Met Office Global cycle {requested} is not complete to +{horizon} h in BADC")
        return value

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        if cycle.tzinfo is None:
            cycle = cycle.replace(tzinfo=UTC)
        if cycle.hour not in {0, 12}:
            return False
        try:
            for field_name in self.FIELD_NAMES.values():
                for area in self.AREAS:
                    path = self._field_path(cycle, field_name, area, horizon)
                    _validate_local_grib_cycle(path, cycle)
            for field_name in ("accumulated_dynamic_rain", "accumulated_convective_rain"):
                for area in self.AREAS:
                    path = self._field_path(cycle, field_name, area, horizon)
                    _validate_local_grib_cycle(path, cycle)
            # A handful of otherwise present files omit one accumulation
            # interval.  Audit one representative tile/component here so such
            # cycles are described as BADC source gaps rather than tracker
            # failures or silently patched precipitation.
            rain_path = self._field_path(cycle, "accumulated_dynamic_rain", self.AREAS[0], horizon)
            rain_records = sorted(_read_local_grib(rain_path, cycle, horizon), key=lambda item: item.end_step)
            previous_end = 0
            for record in rain_records:
                if record.end_step <= 0:
                    continue
                if record.start_step != previous_end:
                    return False
                previous_end = record.end_step
                if previous_end >= horizon:
                    break
            if previous_end < horizon:
                return False
            return True
        except DownloadError:
            return False

    def _records_by_area(self, cycle: datetime, field_name: str, maximum_step: int) -> dict[str, list[LocalGribRecord]]:
        return {
            area: _read_local_grib(
                self._field_path(cycle, field_name, area, maximum_step), cycle, maximum_step
            )
            for area in self.AREAS
        }

    def _instantaneous(self, cycle: datetime, field_name: str, steps: Sequence[int]) -> np.ndarray:
        records = self._records_by_area(cycle, field_name, int(max(steps)))
        lookups = {
            area: {record.end_step: record for record in values}
            for area, values in records.items()
        }
        output: list[np.ndarray] = []
        for step in steps:
            messages = []
            for area in self.AREAS:
                record = lookups[area].get(int(step))
                if record is None:
                    raise DownloadError(f"BADC {field_name} Area{area} lacks +{int(step)} h")
                messages.append(record.payload)
            output.append(decode_grib_messages(messages).values)
        return np.stack(output)

    def _precipitation(self, cycle: datetime, steps: Sequence[int]) -> np.ndarray:
        maximum_step = int(max(steps))
        components: dict[str, dict[tuple[int, int], np.ndarray]] = {}
        for field_name in ("accumulated_dynamic_rain", "accumulated_convective_rain"):
            records = self._records_by_area(cycle, field_name, maximum_step)
            by_area = {
                area: {(record.start_step, record.end_step): record for record in values}
                for area, values in records.items()
            }
            intervals = sorted(set(by_area[self.AREAS[0]]) & set(by_area[self.AREAS[1]]))
            components[field_name] = {
                interval: np.maximum(
                    decode_grib_messages([by_area[area][interval].payload for area in self.AREAS]).values,
                    0.0,
                )
                for interval in intervals
                if interval[1] > 0
            }
        intervals = sorted(set.intersection(*(set(value) for value in components.values())))
        if not intervals:
            raise DownloadError("BADC precipitation components have no common forecast intervals")
        cumulative = np.zeros((GRID_LATS.size, GRID_LONS.size), dtype=np.float32)
        output = [np.zeros_like(cumulative)]
        target_steps = [int(step) for step in steps]
        next_target = 1
        previous_end = 0
        for start, end in intervals:
            if start != previous_end:
                raise DownloadError(f"BADC precipitation intervals are discontinuous at +{previous_end} to +{start} h")
            cumulative = cumulative + sum(value[(start, end)] for value in components.values())
            previous_end = end
            while next_target < len(target_steps) and target_steps[next_target] == end:
                output.append(cumulative.copy())
                next_target += 1
            if end >= maximum_step:
                break
        if next_target != len(target_steps):
            raise DownloadError(
                f"BADC precipitation only supplied {next_target}/{len(target_steps)} requested cumulative frames"
            )
        return np.stack(output)

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        if member_limit not in {None, 1}:
            raise ValueError("Met Office Global is deterministic; --members must be omitted or one")
        cycle = self.resolve_cycle(requested, int(max(steps)))
        fields = {
            key: self._instantaneous(cycle, field_name, steps)
            for key, field_name in self.FIELD_NAMES.items()
        }
        mslp = np.stack([
            to_mslp_hpa(GridField(frame, "msl", "Pa", str(step), None))
            for frame, step in zip(fields["mslp"], steps, strict=True)
        ])
        winds = {
            level: (fields[f"u{level}"], fields[f"v{level}"])
            for level in (850, 700, 500)
        }
        vorticity = {
            level: np.stack([
                relative_vorticity_x1e5(u, v)
                for u, v in zip(winds[level][0], winds[level][1], strict=True)
            ])
            for level in (850, 700, 500)
        }
        precipitation = self._precipitation(cycle, steps)
        tracking = track_forecast_member(
            cycle=cycle,
            steps=steps,
            member="det",
            role="deterministic",
            mslp_hpa=mslp,
            vorticity_by_level=vorticity,
            wind_by_level=winds,
            wind_10m=(fields["u10"], fields["v10"]),
            precipitation_cumulative_mm=precipitation,
        )
        payload = self._payload(
            cycle,
            steps,
            tracking.tracks,
            ["det"],
            vorticity[850],
            precipitation,
            [],
            [{
                "member": "det",
                "detector_candidates": tracking.detector_candidates,
                "linker": tracking.linker_summary,
                "crosscheck": tracking.qa_crosscheck,
            }],
        )
        payload["source"]["retrieval"] = (
            "local BADC multi-message GRIB; Area B/F tiles joined and nearest-neighbour sampled to 1 degree"
        )
        return payload


class _S3RangeFile(io.RawIOBase):
    """Seekable read-only S3 object backed by cached anonymous byte ranges."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        key: str,
        *,
        block_size: int = 128 * 1024,
        max_blocks: int = 384,
    ):
        super().__init__()
        self.client = client
        self.bucket = bucket
        self.key = key
        self.block_size = int(block_size)
        self.max_blocks = int(max_blocks)
        self.size = int(client.head_object(Bucket=bucket, Key=key)["ContentLength"])
        self.position = 0
        self.cache: OrderedDict[int, bytes] = OrderedDict()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self.position + offset
        elif whence == io.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError(f"unsupported seek mode {whence}")
        if position < 0:
            raise ValueError("negative seek position")
        self.position = min(self.size, int(position))
        return self.position

    def _block(self, index: int) -> bytes:
        cached = self.cache.pop(index, None)
        if cached is not None:
            self.cache[index] = cached
            return cached
        start = index * self.block_size
        end = min(self.size, start + self.block_size) - 1
        response = self.client.get_object(
            Bucket=self.bucket,
            Key=self.key,
            Range=f"bytes={start}-{end}",
        )
        data = response["Body"].read()
        expected = end - start + 1
        if len(data) != expected:
            raise DownloadError(
                f"Truncated S3 byte range for s3://{self.bucket}/{self.key}: "
                f"expected {expected}, got {len(data)}"
            )
        self.cache[index] = data
        while len(self.cache) > self.max_blocks:
            self.cache.popitem(last=False)
        return data

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.size - self.position
        remaining = min(int(size), self.size - self.position)
        if remaining <= 0:
            return b""
        parts: list[bytes] = []
        while remaining:
            index = self.position // self.block_size
            offset = self.position % self.block_size
            block = self._block(index)
            take = min(remaining, len(block) - offset)
            if take <= 0:
                raise DownloadError(f"Invalid S3 range cursor for {self.key}")
            parts.append(block[offset:offset + take])
            self.position += take
            remaining -= take
        return b"".join(parts)


class MogrepsAdapter(BaseAdapter):
    """Current MOGREPS-G ensemble from the Met Office rolling AWS archive.

    The provider files are large chunked NetCDF/HDF5 objects.  The adapter
    exposes them to h5py as seekable anonymous S3 byte ranges, so only chunks
    intersecting the atlas domain, selected pressure levels and members cross
    the network.
    """

    BUCKET = "met-office-global-ensemble-model-data"
    PREFIX = "global-ensemble"
    PRESSURE_LEVELS_PA = (85000, 70000, 50000)
    INSTANT_FIELDS = (
        "pressure_at_mean_sea_level",
        "wind_speed_on_pressure_levels",
        "wind_direction_on_pressure_levels",
        "wind_speed_at_10m",
        "wind_direction_at_10m",
    )

    def __init__(self, workers: int = 8, s3_client: Any | None = None):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["mogreps-g"]
        if s3_client is None:
            try:
                import boto3
                from botocore import UNSIGNED
                from botocore.config import Config
            except ImportError as error:
                raise DownloadError("boto3 is required for MOGREPS-G retrieval") from error
            s3_client = boto3.client(
                "s3",
                region_name="eu-west-2",
                config=Config(
                    signature_version=UNSIGNED,
                    connect_timeout=30,
                    read_timeout=120,
                    retries={"max_attempts": 8, "mode": "adaptive"},
                    max_pool_connections=max(20, self.workers * 8),
                ),
            )
        self.s3 = s3_client

    @classmethod
    def _base_prefix(cls, cycle: datetime) -> str:
        return f"{cls.PREFIX}/{cycle:%Y/%m/%d/T%H00Z}"

    @classmethod
    def _instant_key(cls, cycle: datetime, step: int, field: str) -> str:
        valid = cycle + timedelta(hours=int(step))
        return (
            f"{cls._base_prefix(cycle)}/{valid:%Y%m%dT%H%MZ}-"
            f"PT{int(step):04d}H00M-{field}.nc"
        )

    @classmethod
    def _precip_key(cls, cycle: datetime, lead: int) -> str:
        interval = 1 if int(lead) <= 132 else 3
        valid = cycle + timedelta(hours=int(lead))
        return (
            f"{cls._base_prefix(cycle)}/{valid:%Y%m%dT%H%MZ}-"
            f"PT{int(lead):04d}H00M-precipitation_accumulation-PT{interval:02d}H.nc"
        )

    @staticmethod
    def _precip_interval_leads(maximum_step: int) -> list[int]:
        maximum = int(maximum_step)
        return list(range(1, min(maximum, 132) + 1)) + list(range(135, maximum + 1, 3))

    def _exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.BUCKET, Key=key)
            return True
        except Exception:
            return False

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        if cycle.tzinfo is None:
            cycle = cycle.replace(tzinfo=UTC)
        else:
            cycle = cycle.astimezone(UTC)
        if cycle.hour not in {0, 6, 12, 18} or not 0 <= int(horizon) <= 246:
            return False
        keys = [self._instant_key(cycle, horizon, field) for field in self.INSTANT_FIELDS]
        precip_leads = self._precip_interval_leads(horizon)
        if precip_leads:
            keys.append(self._precip_key(cycle, precip_leads[-1]))
        with ThreadPoolExecutor(max_workers=min(self.workers, len(keys))) as executor:
            return all(executor.map(self._exists, keys))

    @staticmethod
    def _member_ids(realizations: np.ndarray) -> list[str]:
        output = []
        for value in np.asarray(realizations, dtype=int).tolist():
            output.append("c00" if value == 0 else f"p{value:02d}")
        return output

    @staticmethod
    def _validate_time(dataset: Any, cycle: datetime, step: int, key: str) -> None:
        expected_reference = int(cycle.timestamp())
        expected_period = int(step) * 3600
        expected_valid = expected_reference + expected_period
        actual = (
            int(dataset["forecast_reference_time"][()]),
            int(dataset["forecast_period"][()]),
            int(dataset["time"][()]),
        )
        expected = (expected_reference, expected_period, expected_valid)
        if actual != expected:
            raise DownloadError(f"MOGREPS-G time metadata {actual} disagrees with {expected} in {key}")

    def _read_region(
        self,
        key: str,
        variable: str,
        cycle: datetime,
        step: int,
        member_count: int,
        *,
        pressure_levels: Sequence[int] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        try:
            import h5py
        except ImportError as error:
            raise DownloadError("h5py is required for MOGREPS-G retrieval") from error
        try:
            with _S3RangeFile(self.s3, self.BUCKET, key) as stream:
                with h5py.File(stream, "r") as dataset:
                    self._validate_time(dataset, cycle, step, key)
                    if variable not in dataset:
                        raise DownloadError(f"MOGREPS-G object {key} lacks {variable}")
                    realizations = np.asarray(dataset["realization"][:member_count], dtype=np.int16)
                    if len(realizations) < member_count:
                        raise DownloadError(
                            f"MOGREPS-G object {key} has {len(realizations)}/{member_count} requested members"
                        )
                    source_lats = np.asarray(dataset["latitude"][:], dtype=np.float32)
                    source_lons = np.asarray(dataset["longitude"][:], dtype=np.float32)
                    lat_indices = np.abs(source_lats[:, None] - GRID_LATS[None, :]).argmin(axis=0)
                    lon_indices = np.abs(source_lons[:, None] - GRID_LONS[None, :]).argmin(axis=0)
                    if (
                        np.max(np.abs(source_lats[lat_indices] - GRID_LATS)) > 0.51
                        or np.max(np.abs(source_lons[lon_indices] - GRID_LONS)) > 0.51
                    ):
                        raise DownloadError(f"MOGREPS-G grid in {key} does not cover the atlas domain")
                    lat_start, lat_stop = int(lat_indices.min()), int(lat_indices.max()) + 1
                    lon_start, lon_stop = int(lon_indices.min()), int(lon_indices.max()) + 1
                    lat_local = lat_indices - lat_start
                    lon_local = lon_indices - lon_start
                    field = dataset[variable]
                    if pressure_levels is None:
                        native = np.asarray(
                            field[:member_count, lat_start:lat_stop, lon_start:lon_stop],
                            dtype=np.float32,
                        )
                    else:
                        pressures = np.asarray(dataset["pressure"][:], dtype=np.float32)
                        level_indices = [int(np.abs(pressures - level).argmin()) for level in pressure_levels]
                        if any(abs(float(pressures[index]) - level) > 1 for index, level in zip(level_indices, pressure_levels, strict=True)):
                            raise DownloadError(f"MOGREPS-G object {key} lacks a requested pressure level")
                        native = np.asarray(
                            field[
                                :member_count,
                                level_indices,
                                lat_start:lat_stop,
                                lon_start:lon_stop,
                            ],
                            dtype=np.float32,
                        )
                    values = native[..., lat_local, :][..., lon_local]
                    values[np.abs(values) > 1.0e10] = np.nan
                    return values, realizations
        except DownloadError:
            raise
        except Exception as error:
            raise DownloadError(f"Could not read MOGREPS-G s3://{self.BUCKET}/{key}: {error}") from error

    @staticmethod
    def _check_realizations(expected: np.ndarray, actual: np.ndarray, key: str) -> None:
        if not np.array_equal(expected, actual):
            raise DownloadError(f"MOGREPS-G realization axis differs in {key}")

    def _load_dynamics_step(
        self,
        cycle: datetime,
        step: int,
        member_count: int,
    ) -> dict[str, Any]:
        msl_key = self._instant_key(cycle, step, "pressure_at_mean_sea_level")
        mslp, realizations = self._read_region(
            msl_key, "air_pressure_at_sea_level", cycle, step, member_count
        )
        speed_key = self._instant_key(cycle, step, "wind_speed_on_pressure_levels")
        speed, values = self._read_region(
            speed_key,
            "wind_speed",
            cycle,
            step,
            member_count,
            pressure_levels=self.PRESSURE_LEVELS_PA,
        )
        self._check_realizations(realizations, values, speed_key)
        direction_key = self._instant_key(cycle, step, "wind_direction_on_pressure_levels")
        direction, values = self._read_region(
            direction_key,
            "wind_from_direction",
            cycle,
            step,
            member_count,
            pressure_levels=self.PRESSURE_LEVELS_PA,
        )
        self._check_realizations(realizations, values, direction_key)
        radians = np.deg2rad(direction)
        pressure_u = -speed * np.sin(radians)
        pressure_v = -speed * np.cos(radians)

        speed10_key = self._instant_key(cycle, step, "wind_speed_at_10m")
        speed10, values = self._read_region(
            speed10_key, "wind_speed", cycle, step, member_count
        )
        self._check_realizations(realizations, values, speed10_key)
        direction10_key = self._instant_key(cycle, step, "wind_direction_at_10m")
        direction10, values = self._read_region(
            direction10_key, "wind_from_direction", cycle, step, member_count
        )
        self._check_realizations(realizations, values, direction10_key)
        radians10 = np.deg2rad(direction10)
        return {
            "step": int(step),
            "realizations": realizations,
            "mslp": mslp / np.float32(100.0),
            "pressure_u": pressure_u.astype(np.float32),
            "pressure_v": pressure_v.astype(np.float32),
            "u10": (-speed10 * np.sin(radians10)).astype(np.float32),
            "v10": (-speed10 * np.cos(radians10)).astype(np.float32),
        }

    def _load_precipitation(
        self,
        cycle: datetime,
        steps: Sequence[int],
        member_count: int,
        expected_realizations: np.ndarray,
    ) -> np.ndarray:
        target_steps = {int(step) for step in steps}
        interval_leads = self._precip_interval_leads(int(max(steps)))
        intervals: dict[int, np.ndarray] = {}

        def load(lead: int) -> tuple[int, np.ndarray]:
            key = self._precip_key(cycle, lead)
            values, realizations = self._read_region(
                key,
                "lwe_thickness_of_precipitation_amount",
                cycle,
                lead,
                member_count,
            )
            self._check_realizations(expected_realizations, realizations, key)
            return lead, np.maximum(values * np.float32(1000.0), 0.0)

        with ThreadPoolExecutor(max_workers=min(self.workers, max(1, len(interval_leads)))) as executor:
            futures = {executor.submit(load, lead): lead for lead in interval_leads}
            for completed, future in enumerate(as_completed(futures), 1):
                lead, values = future.result()
                intervals[lead] = values
                if completed == 1 or completed % 24 == 0 or completed == len(interval_leads):
                    LOGGER.info(
                        "MOGREPS-G %s precipitation intervals %d/%d",
                        cycle_id(cycle), completed, len(interval_leads),
                    )

        cumulative = np.zeros(
            (member_count, GRID_LATS.size, GRID_LONS.size), dtype=np.float32
        )
        selected: dict[int, np.ndarray] = {}
        if 0 in target_steps:
            selected[0] = cumulative.copy()
        for lead in interval_leads:
            cumulative += intervals[lead]
            if lead in target_steps:
                selected[lead] = cumulative.copy()
        missing = sorted(target_steps - set(selected))
        if missing:
            raise DownloadError(f"MOGREPS-G precipitation cannot construct target leads {missing}")
        return np.stack([selected[int(step)] for step in steps])

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        native_steps = [int(step) for step in steps]
        if not native_steps or any(step < 0 or step > 246 or step % 6 for step in native_steps):
            raise ValueError("MOGREPS-G steps must be six-hourly leads from +0 to +246 h")
        if native_steps != sorted(set(native_steps)):
            raise ValueError("MOGREPS-G steps must be unique and ascending")
        member_count = self.definition.expected_members
        if member_limit is not None:
            member_count = max(1, min(int(member_limit), member_count))
        cycle = self.resolve_cycle(requested, int(max(native_steps)))

        dynamics: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(native_steps))) as executor:
            futures = {
                executor.submit(self._load_dynamics_step, cycle, step, member_count): step
                for step in native_steps
            }
            for completed, future in enumerate(as_completed(futures), 1):
                dynamics.append(future.result())
                if completed == 1 or completed % 6 == 0 or completed == len(native_steps):
                    LOGGER.info(
                        "MOGREPS-G %s dynamical leads %d/%d",
                        cycle_id(cycle), completed, len(native_steps),
                    )
        dynamics.sort(key=lambda item: int(item["step"]))
        realizations = dynamics[0]["realizations"]
        for item in dynamics[1:]:
            self._check_realizations(realizations, item["realizations"], f"lead +{item['step']} h")
        members = self._member_ids(realizations)
        precipitation = self._load_precipitation(
            cycle, native_steps, member_count, realizations
        )

        def track_member(member_index: int) -> dict[str, Any]:
            winds = {
                level: (
                    np.stack([item["pressure_u"][member_index, level_index] for item in dynamics]),
                    np.stack([item["pressure_v"][member_index, level_index] for item in dynamics]),
                )
                for level_index, level in enumerate((850, 700, 500))
            }
            vorticity = {
                level: np.stack([
                    relative_vorticity_x1e5(u, v)
                    for u, v in zip(winds[level][0], winds[level][1], strict=True)
                ])
                for level in (850, 700, 500)
            }
            member = members[member_index]
            tracking = track_forecast_member(
                cycle=cycle,
                steps=native_steps,
                member=member,
                role="control" if member == "c00" else "perturbed",
                mslp_hpa=np.stack([item["mslp"][member_index] for item in dynamics]),
                vorticity_by_level=vorticity,
                wind_by_level=winds,
                wind_10m=(
                    np.stack([item["u10"][member_index] for item in dynamics]),
                    np.stack([item["v10"][member_index] for item in dynamics]),
                ),
                precipitation_cumulative_mm=precipitation[:, member_index],
            )
            return {
                "member": member,
                "tracks": tracking.tracks,
                "vorticity": vorticity[850],
                "precipitation": precipitation[:, member_index],
                "tracking_qa": {
                    "member": member,
                    "detector_candidates": tracking.detector_candidates,
                    "linker": tracking.linker_summary,
                    "crosscheck": tracking.qa_crosscheck,
                },
            }

        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(members))) as executor:
            future_map = {
                executor.submit(track_member, index): member
                for index, member in enumerate(members)
            }
            for future in as_completed(future_map):
                member = future_map[future]
                try:
                    results.append(future.result())
                except Exception as error:
                    warnings.append(f"{member} unavailable: {error}")
        results.sort(key=lambda item: members.index(item["member"]))
        minimum = 1 if member_limit is not None else max(3, math.ceil(len(members) * 0.7))
        if len(results) < minimum:
            raise DownloadError(f"Only {len(results)}/{len(members)} MOGREPS-G members completed")
        payload = self._payload(
            cycle,
            native_steps,
            [track for result in results for track in result["tracks"]],
            [result["member"] for result in results],
            np.mean(np.stack([result["vorticity"] for result in results]), axis=0),
            np.mean(np.stack([result["precipitation"] for result in results]), axis=0),
            warnings,
            [result["tracking_qa"] for result in results],
        )
        payload["source"]["retrieval"] = (
            "anonymous S3 HDF5 chunk byte ranges; atlas domain and 850/700/500-hPa levels only; "
            "wind vectors reconstructed from speed/direction and exact native precipitation intervals accumulated"
        )
        return payload


class TiggeAdapter(BaseAdapter):
    """Historical multi-centre ensembles retrieved from the ECDS TIGGE archive."""

    ECDS_URL = "https://ecds.ecmwf.int/api"
    DATASET = "tigge-forecasts"

    def __init__(self, model: str, workers: int = 8):
        super().__init__(workers=workers)
        if model not in TIGGE_CENTRES:
            raise ValueError(f"Unknown TIGGE centre {model!r}")
        self.centre = TIGGE_CENTRES[model]
        self.definition = MODEL_DEFINITIONS[model]
        self.queue_retry_attempts = max(
            1, int(os.environ.get("LPS_TIGGE_QUEUE_RETRY_ATTEMPTS", "40"))
        )
        self.queue_retry_base_seconds = max(
            1.0, float(os.environ.get("LPS_TIGGE_QUEUE_RETRY_BASE_SECONDS", "60"))
        )

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested == "latest":
            raise DownloadError("TIGGE is a delayed historical archive and requires an explicit cycle")
        value = parse_cycle(requested)
        if not self.cycle_complete(value, horizon):
            raise DownloadError(
                f"{self.definition.label} cycle {requested} is outside the supported archive/cadence"
            )
        return value

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        if cycle.tzinfo is None:
            cycle = cycle.replace(tzinfo=UTC)
        return (
            cycle >= self.centre.archive_start
            and cycle.hour in {0, 12}
            and 0 <= horizon <= self.centre.maximum_horizon_hours
        )

    @staticmethod
    def _credentials() -> str:
        config: dict[str, str] = {}
        path = Path.home() / ".cdsapirc"
        for line in path.read_text(encoding="utf-8").splitlines():
            if ":" in line:
                name, value = line.split(":", 1)
                config[name.strip()] = value.strip()
        if not config.get("key"):
            raise DownloadError(f"ECDS credentials are missing from {path}")
        return config["key"]

    @staticmethod
    def _is_queue_limit_error(error: Exception) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "number of queued requests per user",
                "user_queued_limit_exceeded",
                "queued request limit",
                "too many queued requests",
            )
        )

    def _retrieve(self, cycle: datetime, steps: Sequence[int], target: Path, forecast_type: str, levtype: str) -> None:
        try:
            import cdsapi
        except ImportError as error:
            raise DownloadError("cdsapi is required for TIGGE retrieval") from error
        request = {
            "class": "ti",
            "date": cycle.strftime("%Y-%m-%d"),
            "expver": "prod",
            "grid": "1/1",
            "area": "45/45/-15/120",
            "levtype": levtype,
            "origin": self.centre.archive_origin,
            "param": "131/132" if levtype == "pl" else "151/165/166/228",
            "step": "/".join(str(int(step)) for step in steps),
            "time": cycle.strftime("%H:00:00"),
            "type": forecast_type,
        }
        if levtype == "pl":
            request["levelist"] = "500/700/850"
        for attempt in range(1, self.queue_retry_attempts + 1):
            try:
                client = cdsapi.Client(
                    url=self.ECDS_URL, key=self._credentials(), quiet=True
                )
                client.retrieve(self.DATASET, request, str(target))
                return
            except Exception as error:
                retryable = self._is_queue_limit_error(error)
                if not retryable or attempt >= self.queue_retry_attempts:
                    raise
                target.unlink(missing_ok=True)
                delay = min(
                    600.0,
                    self.queue_retry_base_seconds * (2 ** min(attempt - 1, 4)),
                ) + random.uniform(0.0, self.queue_retry_base_seconds)
                LOGGER.warning(
                    "ECDS queue full for %s %s %s/%s; retry %d/%d in %.0f s",
                    self.definition.label,
                    cycle_id(cycle),
                    forecast_type,
                    levtype,
                    attempt + 1,
                    self.queue_retry_attempts,
                    delay,
                )
                time.sleep(delay)

    def _cma_cache_paths(self, cycle: datetime) -> list[Path]:
        root = os.environ.get("LPS_CMA_TIGGE_CACHE", "").strip()
        if not root or self.definition.id not in {"tigge-ukmo", "tigge-imd", "tigge-ncmrwf"}:
            return []
        folder = Path(root) / self.definition.id / cycle_id(cycle)
        if not folder.is_dir():
            return []
        suffixes = {".grib", ".grb", ".grib2", ".grb2"}
        paths = sorted(
            path for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        )
        components = {path.relative_to(folder).parts[0] for path in paths if path.relative_to(folder).parts}
        return paths if {"pressure", "surface"}.issubset(components) else []

    @staticmethod
    def _read_fields(paths: Sequence[Path], cycle: datetime) -> dict[tuple[str, int, str, int], GridField]:
        output: dict[tuple[str, int, str, int], GridField] = {}
        for path in paths:
            with path.open("rb") as stream:
                while True:
                    handle = codes_grib_new_from_file(stream)
                    if handle is None:
                        break
                    try:
                        data_date = int(codes_get(handle, "dataDate"))
                        data_time = int(codes_get(handle, "dataTime"))
                        if data_date != int(cycle.strftime("%Y%m%d")) or data_time != int(cycle.strftime("%H%M")):
                            raise DownloadError(f"TIGGE header date disagrees with {cycle:%Y%m%d%H} in {path}")
                        forecast_type = str(codes_get(handle, "type"))
                        if forecast_type == "cf":
                            member = "c00"
                        elif forecast_type == "fc":
                            member = "h00"
                        else:
                            number = int(codes_get(handle, "perturbationNumber"))
                            member = f"p{number:02d}"
                        step = int(codes_get(handle, "endStep"))
                        short_name = str(codes_get(handle, "shortName"))
                        try:
                            level = int(codes_get(handle, "level"))
                        except Exception:
                            level = 0
                        field = decode_grib_message(bytes(codes_get_message(handle)))
                    finally:
                        codes_release(handle)
                    key = (member, step, short_name, level)
                    if key in output:
                        raise DownloadError(f"Duplicate TIGGE field {key} in {path}")
                    output[key] = field
        return output

    @staticmethod
    def _member_ids(fields: dict[tuple[str, int, str, int], GridField]) -> list[str]:
        values = {key[0] for key in fields}
        return sorted(
            values,
            key=lambda value: (
                0 if value == "c00" else 1 if value == "h00" else 2,
                int(value[1:]),
            ),
        )

    def _load_member(
        self,
        cycle: datetime,
        steps: Sequence[int],
        member: str,
        fields: dict[tuple[str, int, str, int], GridField],
    ) -> dict[str, Any]:
        def values(short_name: str, level: int = 0) -> np.ndarray:
            frames = []
            for step in steps:
                key = (member, int(step), short_name, level)
                if key not in fields:
                    raise DownloadError(f"TIGGE member {member} lacks {short_name}/{level} at +{int(step)} h")
                frames.append(fields[key].values)
            return np.stack(frames)

        mslp = np.stack([
            to_mslp_hpa(fields[(member, int(step), "msl", 0)])
            for step in steps
        ])
        winds = {
            level: (values("u", level), values("v", level))
            for level in (850, 700, 500)
        }
        vorticity = {
            level: np.stack([
                relative_vorticity_x1e5(u, v)
                for u, v in zip(winds[level][0], winds[level][1], strict=True)
            ])
            for level in (850, 700, 500)
        }
        # TIGGE contributors do not all encode total precipitation at t+0 and
        # occasional historical steps are absent. Precipitation is not a
        # detector/classification input, so initialise it at zero and carry the
        # last cumulative value across a missing frame rather than discarding an
        # otherwise complete dynamical member.
        precipitation_frames: list[np.ndarray] = []
        for step in steps:
            key = (member, int(step), "tp", 0)
            if key in fields:
                precipitation_frames.append(to_precip_mm(fields[key]))
            elif precipitation_frames:
                precipitation_frames.append(precipitation_frames[-1].copy())
            else:
                precipitation_frames.append(np.zeros_like(mslp[0], dtype=np.float32))
        precipitation = np.maximum.accumulate(np.stack(precipitation_frames), axis=0)
        role = "control" if member in {"c00", "h00"} else "perturbed"
        tracking = track_forecast_member(
            cycle=cycle,
            steps=steps,
            member=member,
            role=role,
            mslp_hpa=mslp,
            vorticity_by_level=vorticity,
            wind_by_level=winds,
            wind_10m=(values("10u", 10), values("10v", 10)),
            precipitation_cumulative_mm=precipitation,
        )
        return {
            "member": member,
            "tracks": tracking.tracks,
            "vorticity": vorticity[850],
            "precipitation": precipitation,
            "tracking_qa": {
                "member": member,
                "detector_candidates": tracking.detector_candidates,
                "linker": tracking.linker_summary,
                "crosscheck": tracking.qa_crosscheck,
            },
        }

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        cycle = self.resolve_cycle(requested, int(max(steps)))
        cached_paths = self._cma_cache_paths(cycle)
        if cached_paths:
            fields = self._read_fields(cached_paths, cycle)
        else:
            with tempfile.TemporaryDirectory(prefix=f"mla-tigge-{cycle_id(cycle)}-") as directory:
                root = Path(directory)
                requests = [
                    (forecast_type, levtype, root / f"{forecast_type}-{levtype}.grib")
                    for forecast_type in self.centre.forecast_types
                    for levtype in ("pl", "sfc")
                ]
                # ECDS stages pressure-level/surface and control/perturbed requests
                # independently. Submit those independent pieces together so one
                # cycle takes roughly one staging window instead of up to four;
                # _retrieve's bounded queue-limit backoff remains the safety valve.
                with ThreadPoolExecutor(max_workers=min(self.workers, len(requests))) as executor:
                    futures = [
                        executor.submit(
                            self._retrieve,
                            cycle,
                            steps,
                            target,
                            forecast_type,
                            levtype,
                        )
                        for forecast_type, levtype, target in requests
                    ]
                    for future in as_completed(futures):
                        future.result()
                paths = [target for unused_type, unused_level, target in requests]
                fields = self._read_fields(paths, cycle)

        members = self._member_ids(fields)
        if member_limit is not None:
            members = members[:max(1, member_limit)]
        if "cf" in self.centre.forecast_types and "c00" not in members:
            raise DownloadError(f"{self.definition.label} control member is missing")
        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(members))) as executor:
            future_map = {
                executor.submit(self._load_member, cycle, steps, member, fields): member
                for member in members
            }
            for future in as_completed(future_map):
                member = future_map[future]
                try:
                    results.append(future.result())
                except Exception as error:
                    warnings.append(f"{member} unavailable: {error}")
        results.sort(key=lambda item: members.index(item["member"]))
        minimum = 1 if member_limit is not None else max(3, math.ceil(len(members) * 0.7))
        if len(results) < minimum:
            raise DownloadError(
                f"Only {len(results)}/{len(members)} {self.definition.label} members completed"
            )
        payload = self._payload(
            cycle,
            steps,
            [track for result in results for track in result["tracks"]],
            [result["member"] for result in results],
            np.mean(np.stack([result["vorticity"] for result in results]), axis=0),
            np.mean(np.stack([result["precipitation"] for result in results]), axis=0),
            warnings,
            [result["tracking_qa"] for result in results],
            expected_members=len(members),
        )
        member_scope = (
            "all available perturbed members"
            if self.centre.forecast_types == ("pf",)
            else "all available control/perturbed members"
        )
        if cached_paths:
            payload["source"].update({
                "service": "CMA synchronized TIGGE portal",
                "url": "http://tigge.cma.cn/",
                "retrieval": (
                    f"CMA-staged TIGGE {self.definition.centre} GRIB cache, resampled to 1 degree; "
                    f"{member_scope}"
                ),
            })
        else:
            payload["source"]["retrieval"] = (
                f"ECMWF ECDS TIGGE {self.definition.centre} subset at 1 degree; {member_scope}"
            )
        return payload


class TiggeEcmwfAdapter(TiggeAdapter):
    """Backward-compatible name for the original ECMWF-only TIGGE adapter."""

    def __init__(self, workers: int = 8):
        super().__init__("tigge-ecmwf", workers=workers)


class WeatherBenchHresAdapter(BaseAdapter):
    """Historical deterministic IFS forecasts from WeatherBench 2."""

    START = WEATHERBENCH_HRES_START
    END = WEATHERBENCH_HRES_END
    STORE = (
        "weatherbench2/datasets/hres/"
        "2016-2022-0012-240x121_equiangular_with_poles_conservative.zarr"
    )
    HORIZON = 240

    def __init__(self, workers: int = 8):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["ifs"]

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return self.START <= value <= self.END and value.hour in {0, 12} and 0 <= horizon <= self.HORIZON

    @staticmethod
    def _array(dataset: Any, variable: str, *, level: int | None = None) -> np.ndarray:
        values = dataset[variable]
        if level is not None:
            values = values.sel(level=level)
        return np.asarray(
            values.transpose("prediction_timedelta", "latitude", "longitude").values,
            dtype=np.float32,
        )

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        try:
            import gcsfs
            import xarray as xr
        except ImportError as error:
            raise DownloadError("gcsfs and xarray are required for WeatherBench retrieval") from error
        if member_limit not in {None, 1}:
            raise DownloadError("deterministic WeatherBench HRES has one member")

        cycle = self.resolve_cycle(requested, int(max(steps)))
        filesystem = gcsfs.GCSFileSystem(token="anon")
        dataset = xr.open_zarr(
            filesystem.get_mapper(self.STORE),
            consolidated=True,
            decode_timedelta=True,
        )
        selected = dataset[
            [
                "mean_sea_level_pressure",
                "10m_u_component_of_wind",
                "10m_v_component_of_wind",
                "total_precipitation_6hr",
                "u_component_of_wind",
                "v_component_of_wind",
            ]
        ].sel(
            time=np.datetime64(cycle.replace(tzinfo=None)),
            prediction_timedelta=[np.timedelta64(int(step), "h") for step in steps],
            level=[500, 700, 850],
            longitude=slice(float(GRID_LONS[0]) - 1.5, float(GRID_LONS[-1]) + 1.5),
            latitude=slice(float(GRID_LATS[0]) - 1.5, float(GRID_LATS[-1]) + 1.5),
        ).load()
        selected = selected.interp(
            longitude=np.asarray(GRID_LONS, dtype=np.float64),
            latitude=np.asarray(GRID_LATS, dtype=np.float64),
            method="linear",
        )

        mslp = self._array(selected, "mean_sea_level_pressure") / np.float32(100.0)
        u10 = self._array(selected, "10m_u_component_of_wind")
        v10 = self._array(selected, "10m_v_component_of_wind")
        precipitation_6h = np.nan_to_num(
            self._array(selected, "total_precipitation_6hr") * np.float32(1000.0),
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
        cumulative_precipitation = np.cumsum(
            np.maximum(precipitation_6h, 0.0), axis=0, dtype=np.float32
        )
        winds = {
            level: (
                self._array(selected, "u_component_of_wind", level=level),
                self._array(selected, "v_component_of_wind", level=level),
            )
            for level in (850, 700, 500)
        }
        vorticity = {
            level: np.stack([
                relative_vorticity_x1e5(u_frame, v_frame)
                for u_frame, v_frame in zip(values[0], values[1], strict=True)
            ])
            for level, values in winds.items()
        }
        tracking = track_forecast_member(
            cycle=cycle,
            steps=steps,
            member="det",
            role="deterministic",
            mslp_hpa=mslp,
            vorticity_by_level=vorticity,
            wind_by_level=winds,
            wind_10m=(u10, v10),
            precipitation_cumulative_mm=cumulative_precipitation,
        )
        payload = self._payload(
            cycle,
            steps,
            tracking.tracks,
            ["det"],
            vorticity[850],
            cumulative_precipitation,
            [],
            [{
                "member": "det",
                "detector_candidates": tracking.detector_candidates,
                "linker": tracking.linker_summary,
                "crosscheck": tracking.qa_crosscheck,
            }],
            expected_members=1,
        )
        payload["provider_maximum_available_lead"] = (
            [int(step) for step in steps] == list(range(0, self.HORIZON + 1, 6))
        )
        payload["source"] = {
            "provider": "ECMWF via WeatherBench 2",
            "service": "WeatherBench 2 public IFS HRES archive",
            "url": "https://weatherbench2.readthedocs.io/en/latest/data-guide.html#ifs-hres",
            "licence": "research use; see the source dataset licence",
            "retrieval": (
                "public Google Cloud Zarr on the WeatherBench 1.5-degree grid; "
                "linearly interpolated to the common 1-degree atlas grid before derivatives"
            ),
        }
        payload["model_version"] = model_version("tigge-ecmwf", cycle)
        payload["qa"] = validate_cycle_payload(payload)
        if payload["qa"]["status"] == "failed":
            raise ValueError(f"{self.definition.id} payload failed QA: {payload['qa']['errors']}")
        return payload


class EcmwfHresHybridAdapter(BaseAdapter):
    """Route 2016--2022 explicit IFS cycles to WeatherBench and Latest to Open Data."""

    def __init__(self, workers: int = 8):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["ifs"]
        self.weatherbench = WeatherBenchHresAdapter(workers=workers)
        self.live = EcmwfAdapter("ifs", workers=workers)

    @staticmethod
    def _uses_weatherbench(cycle: datetime) -> bool:
        value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return WeatherBenchHresAdapter.START <= value <= WeatherBenchHresAdapter.END

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested == "latest":
            return self.live.resolve_cycle(requested, horizon)
        cycle = parse_cycle(requested)
        adapter = self.weatherbench if self._uses_weatherbench(cycle) else self.live
        return adapter.resolve_cycle(requested, horizon)

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        adapter = self.weatherbench if self._uses_weatherbench(cycle) else self.live
        return adapter.cycle_complete(cycle, horizon)

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        if requested == "latest":
            return self.live.build(requested, steps, member_limit=member_limit)
        cycle = parse_cycle(requested)
        adapter = self.weatherbench if self._uses_weatherbench(cycle) else self.live
        return adapter.build(requested, steps, member_limit=member_limit)


class TiggeWeatherBenchAdapter(BaseAdapter):
    """ECMWF ENS from WeatherBench 2's public cloud-optimised copy.

    WeatherBench stores the 2018--2022 TIGGE ensemble in eight-lead chunks, so
    one cloud read supplies all 50 perturbed members.  Its public compact grid
    is 1.5 degrees; fields are linearly interpolated to the tracker's common
    1-degree grid before any derivatives or detection are calculated.
    """

    START = WEATHERBENCH_IFS_ENS_START
    END = WEATHERBENCH_IFS_ENS_END
    STORE = (
        "weatherbench2/datasets/ifs_ens/"
        "2018-2022-240x121_equiangular_with_poles_conservative.zarr"
    )

    def __init__(self, workers: int = 8):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["tigge-ecmwf"]

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return self.START <= value <= self.END and value.hour in {0, 12} and 0 <= horizon <= 360

    @staticmethod
    def _array(dataset: Any, variable: str, *, level: int | None = None) -> np.ndarray:
        values = dataset[variable]
        if level is not None:
            values = values.sel(level=level)
        return np.asarray(
            values.transpose("number", "prediction_timedelta", "latitude", "longitude").values,
            dtype=np.float32,
        )

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        try:
            import gcsfs
            import xarray as xr
        except ImportError as error:
            raise DownloadError("gcsfs and xarray are required for WeatherBench retrieval") from error

        cycle = self.resolve_cycle(requested, int(max(steps)))
        filesystem = gcsfs.GCSFileSystem(token="anon")
        dataset = xr.open_zarr(
            filesystem.get_mapper(self.STORE),
            consolidated=True,
            decode_timedelta=True,
        )
        numbers = np.asarray(dataset.number.values)
        if member_limit is not None:
            numbers = numbers[:max(1, member_limit)]
        selected = dataset[
            [
                "mean_sea_level_pressure",
                "10m_u_component_of_wind",
                "10m_v_component_of_wind",
                "total_precipitation",
                "u_component_of_wind",
                "v_component_of_wind",
            ]
        ].sel(
            time=np.datetime64(cycle.replace(tzinfo=None)),
            number=numbers,
            prediction_timedelta=[np.timedelta64(int(step), "h") for step in steps],
            level=[500, 700, 850],
            longitude=slice(float(GRID_LONS[0]) - 1.5, float(GRID_LONS[-1]) + 1.5),
            latitude=slice(float(GRID_LATS[0]) - 1.5, float(GRID_LATS[-1]) + 1.5),
        ).load()
        selected = selected.interp(
            longitude=np.asarray(GRID_LONS, dtype=np.float64),
            latitude=np.asarray(GRID_LATS, dtype=np.float64),
            method="linear",
        )

        mslp = self._array(selected, "mean_sea_level_pressure") / np.float32(100.0)
        u10 = self._array(selected, "10m_u_component_of_wind")
        v10 = self._array(selected, "10m_v_component_of_wind")
        precipitation = np.maximum(
            self._array(selected, "total_precipitation") * np.float32(1000.0),
            0.0,
        )
        precipitation = np.maximum.accumulate(precipitation, axis=1)
        winds = {
            level: (
                self._array(selected, "u_component_of_wind", level=level),
                self._array(selected, "v_component_of_wind", level=level),
            )
            for level in (850, 700, 500)
        }
        vorticity = {
            level: np.stack([
                np.stack([
                    relative_vorticity_x1e5(u_frame, v_frame)
                    for u_frame, v_frame in zip(member_u, member_v, strict=True)
                ])
                for member_u, member_v in zip(winds[level][0], winds[level][1], strict=True)
            ])
            for level in (850, 700, 500)
        }
        members = [f"p{int(number):02d}" for number in numbers]

        def load_member(index: int, member: str) -> dict[str, Any]:
            tracking = track_forecast_member(
                cycle=cycle,
                steps=steps,
                member=member,
                role="perturbed",
                mslp_hpa=mslp[index],
                vorticity_by_level={level: values[index] for level, values in vorticity.items()},
                wind_by_level={
                    level: (values[0][index], values[1][index])
                    for level, values in winds.items()
                },
                wind_10m=(u10[index], v10[index]),
                precipitation_cumulative_mm=precipitation[index],
            )
            return {
                "member": member,
                "tracks": tracking.tracks,
                "vorticity": vorticity[850][index],
                "precipitation": precipitation[index],
                "tracking_qa": {
                    "member": member,
                    "detector_candidates": tracking.detector_candidates,
                    "linker": tracking.linker_summary,
                    "crosscheck": tracking.qa_crosscheck,
                },
            }

        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(members))) as executor:
            future_map = {
                executor.submit(load_member, index, member): member
                for index, member in enumerate(members)
            }
            for future in as_completed(future_map):
                member = future_map[future]
                try:
                    results.append(future.result())
                except Exception as error:
                    warnings.append(f"{member} unavailable: {error}")
        results.sort(key=lambda item: members.index(item["member"]))
        minimum = 1 if member_limit is not None else max(3, math.ceil(len(members) * 0.7))
        if len(results) < minimum:
            raise DownloadError(
                f"Only {len(results)}/{len(members)} {self.definition.label} WeatherBench members completed"
            )
        payload = self._payload(
            cycle,
            steps,
            [track for result in results for track in result["tracks"]],
            [result["member"] for result in results],
            np.mean(np.stack([result["vorticity"] for result in results]), axis=0),
            np.mean(np.stack([result["precipitation"] for result in results]), axis=0),
            warnings,
            [result["tracking_qa"] for result in results],
            expected_members=50,
        )
        payload["source"] = {
            "provider": "ECMWF via WeatherBench 2",
            "service": "WeatherBench 2 public IFS ENS archive",
            "url": "https://weatherbench2.readthedocs.io/en/latest/data-guide.html#ifs-ens",
            "licence": "research use; source TIGGE terms apply",
            "retrieval": (
                "public Google Cloud Zarr, 50 perturbed members on the WeatherBench 1.5-degree grid; "
                "linearly interpolated to the common 1-degree atlas grid before derivatives"
            ),
        }
        payload["qa"] = validate_cycle_payload(payload)
        if payload["qa"]["status"] == "failed":
            raise ValueError(f"{self.definition.id} payload failed QA: {payload['qa']['errors']}")
        return payload


class TiggeEcmwfHybridAdapter(BaseAdapter):
    """Route 2018--2022 ECMWF ENS cycles to WeatherBench, others to ECDS."""

    def __init__(self, workers: int = 8):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["tigge-ecmwf"]
        self.weatherbench = TiggeWeatherBenchAdapter(workers=workers)
        self.ecds = TiggeAdapter("tigge-ecmwf", workers=workers)

    @staticmethod
    def _uses_weatherbench(cycle: datetime) -> bool:
        value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return TiggeWeatherBenchAdapter.START <= value <= TiggeWeatherBenchAdapter.END

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested == "latest":
            raise DownloadError("ECMWF TIGGE is a historical archive and requires an explicit cycle")
        cycle = parse_cycle(requested)
        adapter = self.weatherbench if self._uses_weatherbench(cycle) else self.ecds
        return adapter.resolve_cycle(requested, horizon)

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        adapter = self.weatherbench if self._uses_weatherbench(cycle) else self.ecds
        return adapter.cycle_complete(cycle, horizon)

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        cycle = parse_cycle(requested) if requested != "latest" else self.resolve_cycle(requested, int(max(steps)))
        adapter = self.weatherbench if self._uses_weatherbench(cycle) else self.ecds
        return adapter.build(requested, steps, member_limit=member_limit)


class NcepAdapter(BaseAdapter):
    LIVE_GFS_ROOT = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
    LIVE_GEFS_ROOT = "https://noaa-gefs-pds.s3.amazonaws.com"
    LIVE_AI_ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com"
    GFS_V16_START = datetime(2021, 3, 22, 12)
    GEFS_V12_START = datetime(2020, 9, 23, 12)
    GEFS_FOLDER_START = datetime(2018, 7, 27, 0)

    def __init__(
        self,
        model: str,
        client: HttpClient | None = None,
        workers: int = 16,
        archive_root: str | None = None,
    ):
        if model not in {"gfs", "gefs", "gefs-control", "aigfs", "aigefs"}:
            raise ValueError(model)
        super().__init__(client, workers)
        self.definition = MODEL_DEFINITIONS[model]
        self.archive_root = archive_root

    def _urls(
        self,
        cycle: datetime,
        step: int,
        member: str = "det",
        field_group: str | None = None,
    ) -> tuple[str, str]:
        date = cycle.strftime("%Y%m%d")
        hour = cycle.strftime("%H")
        if self.definition.id in {"aigfs", "aigefs"}:
            if self.archive_root:
                raise ValueError("NOAA AI forecast archive roots are not configurable")
            if field_group not in {"pres", "sfc"}:
                raise ValueError("AIGFS/AIGEFS URLs require field_group='pres' or 'sfc'")
            model = self.definition.id
            if model == "aigfs":
                folder = f"{model}.{date}/{hour}/model/atmos/grib2"
            else:
                if not re.fullmatch(r"p\d{2}", member):
                    raise ValueError(f"Invalid AIGEFS member {member!r}")
                folder = (
                    f"{model}.{date}/{hour}/mem{int(member[1:]):03d}/"
                    "model/atmos/grib2"
                )
            base = (
                f"{self.LIVE_AI_ROOT}/{model}/prod/{folder}/"
                f"{model}.t{hour}z.{field_group}.f{step:03d}.grib2"
            )
            return base, f"{base}.idx"
        if self.archive_root:
            if self.definition.id != "gfs":
                raise ValueError("The NOAA archive adapter currently supports GFS only")
            if "noaa-gfs-bdp-pds" in self.archive_root:
                base = (
                    f"{self.archive_root}/gfs.{date}/{hour}/atmos/"
                    f"gfs.t{hour}z.pgrb2.0p25.f{step:03d}"
                )
                return base, f"{base}.idx"
            base = (
                f"{self.archive_root}/global-forecast-system/access/grid-004-0.5-degree/forecast/"
                f"{cycle:%Y%m}/{date}/gfs_4_{date}_{hour}00_{step:03d}.grb2"
            )
            return base, f"{base}.inv"
        if self.definition.id == "gfs":
            middle = "/atmos" if cycle.replace(tzinfo=None) >= self.GFS_V16_START else ""
            base = f"{self.LIVE_GFS_ROOT}/gfs.{date}/{hour}{middle}/gfs.t{hour}z.pgrb2.0p25.f{step:03d}"
            return base, f"{base}.idx"
        prefix = "gec00" if member == "c00" else f"ge{member}"
        naive_cycle = cycle.replace(tzinfo=None)
        if naive_cycle >= self.GEFS_V12_START:
            base = (
                f"{self.LIVE_GEFS_ROOT}/gefs.{date}/{hour}/atmos/pgrb2ap5/"
                f"{prefix}.t{hour}z.pgrb2a.0p50.f{step:03d}"
            )
        elif naive_cycle >= self.GEFS_FOLDER_START:
            base = f"{self.LIVE_GEFS_ROOT}/gefs.{date}/{hour}/pgrb2a/{prefix}.t{hour}z.pgrb2af{step:02d}"
        else:
            base = f"{self.LIVE_GEFS_ROOT}/gefs.{date}/{hour}/{prefix}.t{hour}z.pgrb2af{step:03d}"
        return base, f"{base}.idx"

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        if self.definition.id in {"aigfs", "aigefs"}:
            member = "det" if self.definition.id == "aigfs" else "p01"
            index_urls = [
                self._urls(cycle, horizon, member, field_group)[1]
                for field_group in ("pres", "sfc")
            ]
        else:
            member = "det" if self.definition.id == "gfs" else "c00"
            index_urls = [self._urls(cycle, horizon, member)[1]]
        try:
            text = "\n".join(self.client.text(index_url) for index_url in index_urls)
            return all(
                token in text
                for token in (
                    "PRMSL:mean sea level",
                    "UGRD:850 mb",
                    "VGRD:850 mb",
                    "UGRD:700 mb",
                    "VGRD:700 mb",
                    "UGRD:500 mb",
                    "VGRD:500 mb",
                    "UGRD:10 m above ground",
                    "VGRD:10 m above ground",
                )
            )
        except DownloadError:
            return False

    def _member_ids(self, cycle: datetime, member_limit: int | None) -> list[str]:
        if self.definition.id in {"gfs", "aigfs"}:
            return ["det"]
        if self.definition.id == "gefs-control":
            return ["c00"]
        if self.definition.id == "aigefs":
            values = [f"p{number:02d}" for number in range(1, 32)]
            return values[: max(1, member_limit)] if member_limit is not None else values
        perturbed_members = 30 if cycle.replace(tzinfo=None) >= self.GEFS_V12_START else 20
        values = ["c00"] + [f"p{number:02d}" for number in range(1, perturbed_members + 1)]
        if member_limit is not None:
            return values[: max(1, member_limit)]
        return values

    def _load_member(self, cycle: datetime, steps: Sequence[int], member: str) -> dict[str, Any]:
        mslp: list[np.ndarray | None] = []
        winds: dict[int, dict[str, list[np.ndarray | None]]] = {
            level: {"u": [], "v": []} for level in (850, 700, 500)
        }
        vorticity: dict[int, list[np.ndarray | None]] = {
            level: [] for level in (850, 700, 500)
        }
        u10_values: list[np.ndarray | None] = []
        v10_values: list[np.ndarray | None] = []
        precipitation: list[np.ndarray | None] = []
        cumulative_flags: list[bool | None] = []
        missing_steps: list[int] = []
        errors: list[str] = []
        for step in steps:
            try:
                if self.definition.id in {"aigfs", "aigefs"}:
                    sources = {
                        field_group: (
                            data_url,
                            parse_ncep_index(self.client.text(index_url)),
                        )
                        for field_group in ("pres", "sfc")
                        for data_url, index_url in [
                            self._urls(cycle, int(step), member, field_group)
                        ]
                    }
                else:
                    data_url, index_url = self._urls(cycle, int(step), member)
                    sources = {
                        "combined": (
                            data_url,
                            parse_ncep_index(self.client.text(index_url)),
                        )
                    }
            except DownloadError as error:
                missing_steps.append(int(step))
                errors.append(f"+{int(step):03d} h: {error}")
                mslp.append(None)
                for level in (850, 700, 500):
                    winds[level]["u"].append(None)
                    winds[level]["v"].append(None)
                    vorticity[level].append(None)
                u10_values.append(None)
                v10_values.append(None)
                precipitation.append(None)
                cumulative_flags.append(None)
                continue
            try:
                def fetch(token: str, *preferred_groups: str) -> GridField:
                    order = list(preferred_groups) + [
                        group for group in sources if group not in preferred_groups
                    ]
                    for group in order:
                        source_url, source_records = sources[group]
                        matches = [
                            record for record in source_records
                            if token in record.description
                        ]
                        if matches:
                            return decode_grib_message(
                                _fetch_record(self.client, source_url, matches[0])
                            )
                    raise DownloadError(f"NCEP inventory lacks {token}")

                msl = to_mslp_hpa(
                    fetch(":PRMSL:mean sea level:", "sfc", "combined")
                )
                mslp.append(msl)
                for level in (850, 700, 500):
                    u = fetch(f":UGRD:{level} mb:", "pres", "combined").values
                    v = fetch(f":VGRD:{level} mb:", "pres", "combined").values
                    winds[level]["u"].append(u)
                    winds[level]["v"].append(v)
                    vorticity[level].append(relative_vorticity_x1e5(u, v))
                u10_values.append(fetch(":UGRD:10 m above ground:", "sfc", "combined").values)
                v10_values.append(fetch(":VGRD:10 m above ground:", "sfc", "combined").values)
                precip_source = next(
                    (
                        (source_url, record)
                        for group in ("sfc", "combined")
                        if group in sources
                        for source_url, source_records in [sources[group]]
                        for record in [_ncep_precip_record(source_records, int(step))]
                        if record is not None
                    ),
                    None,
                )
                if precip_source is None:
                    precip = np.zeros((GRID_LATS.size, GRID_LONS.size), dtype=np.float32)
                    cumulative = True
                else:
                    precip_field = decode_grib_message(
                        _fetch_record(self.client, precip_source[0], precip_source[1])
                    )
                    precip = to_precip_mm(precip_field)
                    cumulative = _precip_is_cumulative(precip_field.step_range, int(step))
                precipitation.append(precip)
                cumulative_flags.append(cumulative)
            except Exception as error:
                raise DownloadError(
                    f"{member} has an unreadable +{int(step):03d} h frame: {error}"
                ) from error

        try:
            mslp_filled = _interpolate_isolated_native_gaps(mslp, steps)
            winds_filled = {
                level: {
                    component: _interpolate_isolated_native_gaps(values, steps)
                    for component, values in components.items()
                }
                for level, components in winds.items()
            }
            vorticity_filled = {
                level: _interpolate_isolated_native_gaps(values, steps)
                for level, values in vorticity.items()
            }
            u10_filled = _interpolate_isolated_native_gaps(u10_values, steps)
            v10_filled = _interpolate_isolated_native_gaps(v10_values, steps)
            precipitation_filled = _interpolate_isolated_native_gaps(precipitation, steps)
        except DownloadError as error:
            raise DownloadError(
                f"{member} incomplete ({len(steps) - len(missing_steps)}/{len(steps)} steps): "
                f"{error}; {'; '.join(errors[:2])}"
            ) from error

        flags_filled: list[bool] = []
        for index, flag in enumerate(cumulative_flags):
            if flag is not None:
                flags_filled.append(bool(flag))
                continue
            # A reconstructed precipitation frame represents the missing
            # native accumulation interval when its neighbours are intervals;
            # cumulative neighbours remain cumulative.
            previous = cumulative_flags[index - 1]
            following = cumulative_flags[index + 1]
            flags_filled.append(bool(previous) if previous == following else False)
        role = "deterministic" if self.definition.kind == "deterministic" else ("control" if member == "c00" else "perturbed")
        cumulative_precipitation = _finalise_precip(precipitation_filled, flags_filled)
        tracking = track_forecast_member(
            cycle=cycle,
            steps=steps,
            member=member,
            role=role,
            mslp_hpa=np.stack(mslp_filled),
            vorticity_by_level={
                level: np.stack(values) for level, values in vorticity_filled.items()
            },
            wind_by_level={
                level: (np.stack(values["u"]), np.stack(values["v"]))
                for level, values in winds_filled.items()
            },
            wind_10m=(np.stack(u10_filled), np.stack(v10_filled)),
            precipitation_cumulative_mm=cumulative_precipitation,
        )
        return {
            "member": member,
            "tracks": tracking.tracks,
            "vorticity": np.stack(vorticity_filled[850]),
            "precipitation": cumulative_precipitation,
            "source_gap_steps": missing_steps,
            "tracking_qa": {
                "member": member,
                "detector_candidates": tracking.detector_candidates,
                "linker": tracking.linker_summary,
                "crosscheck": tracking.qa_crosscheck,
            },
        }

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        cycle = self.resolve_cycle(requested, int(max(steps)))
        members = self._member_ids(cycle, member_limit)
        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(members))) as executor:
            future_map = {executor.submit(self._load_member, cycle, steps, member): member for member in members}
            for future in as_completed(future_map):
                member = future_map[future]
                try:
                    results.append(future.result())
                except Exception as error:
                    warnings.append(f"{member} unavailable: {error}")
        results.sort(key=lambda item: members.index(item["member"]))
        # A deliberately capped development/QA run must be allowed to exercise a
        # single ensemble member. Operational runs still require at least 70% of
        # the configured ensemble, with a floor of three members.
        minimum = (
            1
            if self.definition.kind == "deterministic" or member_limit is not None
            else max(3, math.ceil(len(members) * 0.7))
        )
        if len(results) < minimum:
            LOGGER.error("%s member failures: %s", self.definition.label, "; ".join(warnings[:5]))
            raise DownloadError(f"Only {len(results)}/{len(members)} {self.definition.label} members completed")
        reconstructed = {
            str(result["member"]): [int(step) for step in result.get("source_gap_steps", [])]
            for result in results
            if result.get("source_gap_steps")
        }
        if reconstructed:
            warnings.append(
                "isolated NOAA source gaps reconstructed by linear temporal interpolation: "
                + "; ".join(
                    f"{member} at {', '.join(f'+{step} h' for step in member_steps)}"
                    for member, member_steps in reconstructed.items()
                )
            )
        tracks = [track for result in results for track in result["tracks"]]
        vort_mean = np.mean(np.stack([result["vorticity"] for result in results]), axis=0)
        precip_mean = np.mean(np.stack([result["precipitation"] for result in results]), axis=0)
        payload = self._payload(
            cycle,
            steps,
            tracks,
            [result["member"] for result in results],
            vort_mean,
            precip_mean,
            warnings,
            [result["tracking_qa"] for result in results],
            expected_members=(
                1
                if self.definition.id in {"gfs", "gefs-control", "aigfs"}
                else 31 if cycle.replace(tzinfo=None) >= self.GEFS_V12_START else 21
            ),
        )
        if reconstructed:
            payload["source"]["gap_reconstruction"] = {
                "policy": "linear interpolation of isolated missing six-hour member frames bounded by source-present neighbours",
                "members": reconstructed,
                "reconstructed_member_frames": sum(len(values) for values in reconstructed.values()),
            }
            payload["method"]["source_gap_policy"] = (
                "isolated provider-file gaps are linearly reconstructed between the two adjacent native frames; "
                "leading, trailing and consecutive gaps fail QA"
            )
        return payload


class NoaaGraphCastAdapter(BaseAdapter):
    """GraphCast Operational output from NOAA/CIRA's public AIWP archive.

    The source NetCDF files are multi-gigabyte global cubes, but their HDF5
    variables are independently chunked by lead and level.  Opening them via a
    seekable HTTP file lets h5netcdf request only the detector variables and
    keeps the common 1-degree atlas domain in memory.
    """

    ROOT = "https://noaa-oar-mlwp-data.s3.amazonaws.com"
    START = datetime(2022, 1, 1, 0, tzinfo=UTC)
    HORIZON = 240

    def __init__(self, client: HttpClient | None = None, workers: int = 8):
        super().__init__(client, workers)
        self.definition = MODEL_DEFINITIONS["graphcast-noaa"]

    @classmethod
    def _url(cls, cycle: datetime) -> str:
        return (
            f"{cls.ROOT}/GRAP_v100_GFS/{cycle:%Y}/{cycle:%m%d}/"
            f"GRAP_v100_GFS_{cycle:%Y%m%d%H}_f000_f240_06.nc"
        )

    @staticmethod
    def _supported_cycle(cycle: datetime) -> bool:
        value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value >= NoaaGraphCastAdapter.START and (
            value.hour in {0, 12}
            or (value.year == 2023 and value.hour in {6, 18})
        )

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        return (
            0 <= horizon <= self.HORIZON
            and self._supported_cycle(cycle)
            and self.client.exists(self._url(cycle))
        )

    def build(
        self,
        requested: str,
        steps: Sequence[int],
        member_limit: int | None = None,
    ) -> dict[str, Any]:
        if member_limit not in {None, 1}:
            raise ValueError("GraphCast is deterministic and has one member")
        native_steps = [int(step) for step in steps]
        if not native_steps or native_steps[0] != 0:
            raise DownloadError("GraphCast processing requires the initialization frame")
        if native_steps != list(range(0, native_steps[-1] + 1, 6)):
            raise DownloadError("GraphCast processing requires a complete six-hourly lead axis")
        cycle = self.resolve_cycle(requested, native_steps[-1])
        try:
            import fsspec
            import xarray as xr
        except ImportError as error:
            raise DownloadError("fsspec, xarray and h5netcdf are required for GraphCast") from error

        url = self._url(cycle)
        with fsspec.open(
            url,
            "rb",
            block_size=8 * 1024 * 1024,
            cache_type="readahead",
        ) as stream:
            with xr.open_dataset(
                stream,
                engine="h5netcdf",
                chunks=None,
                decode_times=False,
            ) as dataset:
                required = {"u", "v", "u10", "v10", "msl", "apcp"}
                missing = sorted(required - set(dataset.data_vars))
                if missing:
                    raise DownloadError(f"GraphCast file lacks {', '.join(missing)}")
                source_times = np.asarray(dataset["time"].values, dtype=np.int64)
                expected_times = np.asarray(
                    [int((cycle + timedelta(hours=step)).timestamp()) for step in native_steps],
                    dtype=np.int64,
                )
                lookup = {int(value): index for index, value in enumerate(source_times)}
                if any(int(value) not in lookup for value in expected_times):
                    raise DownloadError("GraphCast file does not contain the requested valid-time axis")
                time_indices = [lookup[int(value)] for value in expected_times]
                if time_indices != list(range(time_indices[0], time_indices[-1] + 1)):
                    raise DownloadError("GraphCast valid-time indexes are not contiguous")

                latitudes = np.asarray(dataset["latitude"].values, dtype=np.float64)
                longitudes = np.mod(
                    np.asarray(dataset["longitude"].values, dtype=np.float64), 360.0
                )

                def coordinate_index(values: np.ndarray, target: float) -> int:
                    index = int(np.argmin(np.abs(values - target)))
                    if abs(float(values[index]) - target) > 1e-4:
                        raise DownloadError(f"GraphCast grid lacks {target:g} degrees")
                    return index

                north = coordinate_index(latitudes, float(GRID_LATS[-1]))
                south = coordinate_index(latitudes, float(GRID_LATS[0]))
                west = coordinate_index(longitudes, float(GRID_LONS[0]))
                east = coordinate_index(longitudes, float(GRID_LONS[-1]))
                if north >= south or west >= east:
                    raise DownloadError("Unexpected GraphCast coordinate ordering")
                latitude_slice = slice(north, south + 1, 4)
                longitude_slice = slice(west, east + 1, 4)
                time_slice = slice(time_indices[0], time_indices[-1] + 1)

                sampled_latitudes = latitudes[latitude_slice][::-1]
                sampled_longitudes = longitudes[longitude_slice]
                if not np.allclose(sampled_latitudes, GRID_LATS) or not np.allclose(
                    sampled_longitudes, GRID_LONS
                ):
                    raise DownloadError("GraphCast 1-degree sample does not match the atlas grid")

                def surface(name: str) -> np.ndarray:
                    values = np.asarray(
                        dataset[name].isel(
                            time=time_slice,
                            latitude=latitude_slice,
                            longitude=longitude_slice,
                        ).values,
                        dtype=np.float32,
                    )
                    return values[:, ::-1, :]

                def pressure_wind(name: str, level: int) -> np.ndarray:
                    level_values = np.asarray(dataset["level"].values, dtype=np.int64)
                    matches = np.flatnonzero(level_values == level)
                    if len(matches) != 1:
                        raise DownloadError(f"GraphCast file lacks a unique {level}-hPa level")
                    values = np.asarray(
                        dataset[name].isel(
                            time=time_slice,
                            level=int(matches[0]),
                            latitude=latitude_slice,
                            longitude=longitude_slice,
                        ).values,
                        dtype=np.float32,
                    )
                    return values[:, ::-1, :]

                winds = {
                    level: (pressure_wind("u", level), pressure_wind("v", level))
                    for level in (850, 700, 500)
                }
                mslp = surface("msl") / np.float32(100.0)
                u10 = surface("u10")
                v10 = surface("v10")
                precipitation_intervals = np.maximum(surface("apcp") * np.float32(1000.0), 0.0)
                precipitation = np.cumsum(precipitation_intervals, axis=0, dtype=np.float32)
                source_version = str(dataset.attrs.get("version", "")).strip()

        vorticity = {
            level: np.stack([
                relative_vorticity_x1e5(u, v)
                for u, v in zip(level_winds[0], level_winds[1], strict=True)
            ])
            for level, level_winds in winds.items()
        }
        tracking = track_forecast_member(
            cycle=cycle,
            steps=native_steps,
            member="det",
            role="deterministic",
            mslp_hpa=mslp,
            vorticity_by_level=vorticity,
            wind_by_level=winds,
            wind_10m=(u10, v10),
            precipitation_cumulative_mm=precipitation,
        )
        payload = self._payload(
            cycle,
            native_steps,
            tracking.tracks,
            ["det"],
            vorticity[850],
            precipitation,
            [],
            [{
                "member": "det",
                "detector_candidates": tracking.detector_candidates,
                "linker": tracking.linker_summary,
                "crosscheck": tracking.qa_crosscheck,
            }],
            expected_members=1,
        )
        payload["source"]["retrieval"] = (
            "public NOAA/CIRA NetCDF HDF5 chunks read by HTTP byte range; "
            "850/700/500-hPa and surface detector variables sampled from 0.25 to 1 degree"
        )
        if source_version:
            payload["model_version"] = {
                "label": f"GraphCast {source_version}",
                "valid_from_utc": None,
                "source_url": self.definition.source_url,
                "basis": "version global attribute in the NOAA/CIRA source NetCDF",
            }
        payload["qa"] = validate_cycle_payload(payload)
        if payload["qa"]["status"] == "failed":
            raise ValueError(f"graphcast-noaa payload failed QA: {payload['qa']['errors']}")
        return payload


class TiggeNcepAdapter(BaseAdapter):
    """NCEP archive adapter using the fastest authoritative source available.

    NOAA's public GEFS object store contains the same NCEP ensemble from 2017
    onward and supports efficient inventory byte-range reads.  Older cycles
    continue to use the ECDS TIGGE service.  Both routes retain the public
    ``tigge-ncep`` identity so the archive has one continuous model series.
    """

    NOAA_START = NOAA_GEFS_ARCHIVE_START

    def __init__(self, workers: int = 16):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["tigge-ncep"]
        self.noaa = NcepAdapter("gefs", client=self.client, workers=workers)
        self.ecds = TiggeAdapter("tigge-ncep", workers=workers)

    @classmethod
    def _uses_noaa(cls, cycle: datetime) -> bool:
        value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
        return value.astimezone(UTC) >= cls.NOAA_START

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested == "latest":
            raise DownloadError("NCEP TIGGE is a historical archive and requires an explicit cycle")
        cycle = parse_cycle(requested)
        adapter = self.noaa if self._uses_noaa(cycle) else self.ecds
        return adapter.resolve_cycle(requested, horizon)

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        adapter = self.noaa if self._uses_noaa(cycle) else self.ecds
        return adapter.cycle_complete(cycle, horizon)

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        cycle = parse_cycle(requested) if requested != "latest" else self.resolve_cycle(requested, int(max(steps)))
        if not self._uses_noaa(cycle):
            return self.ecds.build(requested, steps, member_limit=member_limit)

        payload = self.noaa.build(requested, steps, member_limit=member_limit)
        definition = self.definition
        payload["model"] = {
            "id": definition.id,
            "label": definition.label,
            "centre": definition.centre,
            "kind": definition.kind,
            "description": definition.description,
            "colour": definition.colour,
        }
        gap_reconstruction = payload.get("source", {}).get("gap_reconstruction")
        payload["source"] = {
            "provider": "NOAA/NCEP",
            "service": "NOAA Open Data GEFS archive",
            "url": "https://registry.opendata.aws/noaa-gefs/",
            "licence": "NOAA public data",
            "retrieval": "public S3 inventory byte ranges; atlas domain resampled to 1 degree",
        }
        if gap_reconstruction:
            payload["source"]["gap_reconstruction"] = gap_reconstruction
        # Keep the exact GEFS generation crosswalk produced by NcepAdapter.
        # Only the archive-facing model identifier changes.
        payload["qa"] = validate_cycle_payload(payload)
        if payload["qa"]["status"] == "failed":
            raise ValueError(f"{definition.id} payload failed QA: {payload['qa']['errors']}")
        return payload


def _ecmwf_record(
    records: Sequence[IndexRecord],
    param: str,
    *,
    level: str | None = None,
    number: str | None = None,
) -> IndexRecord:
    matches = []
    for record in records:
        attributes = record.attributes
        if attributes.get("param") != param:
            continue
        if level is not None and attributes.get("levelist") != level:
            continue
        if number is not None and attributes.get("number") != number:
            continue
        matches.append(record)
    if not matches:
        suffix = f" level {level}" if level else ""
        suffix += f" member {number}" if number is not None else ""
        raise DownloadError(f"ECMWF index lacks {param}{suffix}")
    return matches[0]


class EcmwfAdapter(BaseAdapter):
    ROOT = "https://data.ecmwf.int/forecasts"

    def __init__(self, model: str, client: HttpClient | None = None, workers: int = 16):
        if model not in {"ifs", "ifs-ens", "aifs", "aifs-ens"}:
            raise ValueError(model)
        super().__init__(client, workers)
        self.definition = MODEL_DEFINITIONS[model]

    def _file_specs(self, cycle: datetime, step: int) -> list[tuple[str, str, str]]:
        stamp = cycle.strftime("%Y%m%d%H0000")
        root = f"{self.ROOT}/{cycle:%Y%m%d}/{cycle:%H}z"
        if self.definition.id == "ifs":
            base = f"{root}/ifs/0p25/oper/{stamp}-{step}h-oper-fc"
            return [("det", f"{base}.grib2", f"{base}.index")]
        if self.definition.id == "aifs":
            base = f"{root}/aifs-single/0p25/oper/{stamp}-{step}h-oper-fc"
            return [("det", f"{base}.grib2", f"{base}.index")]
        if self.definition.id == "ifs-ens":
            base = f"{root}/ifs/0p25/enfo/{stamp}-{step}h-enfo-ef"
            return [("pf", f"{base}.grib2", f"{base}.index")]
        control = f"{root}/aifs-ens/0p25/enfo/{stamp}-{step}h-enfo-cf"
        perturbed = f"{root}/aifs-ens/0p25/enfo/{stamp}-{step}h-enfo-pf"
        return [
            ("cf", f"{control}.grib2", f"{control}.index"),
            ("pf", f"{perturbed}.grib2", f"{perturbed}.index"),
        ]

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        try:
            specs = self._file_specs(cycle, horizon)
            # The perturbed file is the largest and normally finishes last.
            unused_role, unused_data, index_url = specs[-1]
            text = self.client.text(index_url)
            return all(token in text for token in ('"param": "msl"', '"param": "u"', '"param": "v"'))
        except DownloadError:
            return False

    def _member_ids(self, member_limit: int | None) -> list[str]:
        if self.definition.kind == "deterministic":
            return ["det"]
        if self.definition.id == "ifs-ens":
            values = [f"p{number:02d}" for number in range(1, 51)]
        else:
            values = ["c00"] + [f"p{number:02d}" for number in range(1, 51)]
        if member_limit is not None:
            return values[: max(1, member_limit)]
        return values

    def _indexes_for_steps(self, cycle: datetime, steps: Sequence[int]) -> dict[int, list[tuple[str, str, list[IndexRecord]]]]:
        output: dict[int, list[tuple[str, str, list[IndexRecord]]]] = {}

        def load(step: int) -> tuple[int, list[tuple[str, str, list[IndexRecord]]]]:
            values = []
            for role, data_url, index_url in self._file_specs(cycle, step):
                values.append((role, data_url, parse_ecmwf_index(self.client.text(index_url))))
            return step, values

        with ThreadPoolExecutor(max_workers=min(self.workers, len(steps))) as executor:
            futures = [executor.submit(load, int(step)) for step in steps]
            for future in as_completed(futures):
                step, values = future.result()
                output[step] = values
        return output

    def _records_for_member(
        self,
        specs: list[tuple[str, str, list[IndexRecord]]],
        member: str,
    ) -> tuple[str, list[IndexRecord], str | None]:
        if member == "det":
            unused_role, data_url, records = specs[0]
            return data_url, records, None
        if member == "c00":
            role, data_url, records = next(item for item in specs if item[0] == "cf")
            return data_url, records, None
        role, data_url, records = next(item for item in specs if item[0] == "pf")
        return data_url, records, str(int(member[1:]))

    def _load_member(
        self,
        cycle: datetime,
        steps: Sequence[int],
        member: str,
        indexes: dict[int, list[tuple[str, str, list[IndexRecord]]]],
    ) -> dict[str, Any]:
        mslp: list[np.ndarray] = []
        winds: dict[int, dict[str, list[np.ndarray]]] = {
            level: {"u": [], "v": []} for level in (850, 700, 500)
        }
        vorticity: dict[int, list[np.ndarray]] = {level: [] for level in (850, 700, 500)}
        u10_values: list[np.ndarray] = []
        v10_values: list[np.ndarray] = []
        precipitation: list[np.ndarray] = []
        for step in steps:
            data_url, records, number = self._records_for_member(indexes[int(step)], member)
            msl = to_mslp_hpa(decode_grib_message(_fetch_record(self.client, data_url, _ecmwf_record(records, "msl", number=number))))
            mslp.append(msl)
            for level in (850, 700, 500):
                u = decode_grib_message(
                    _fetch_record(self.client, data_url, _ecmwf_record(records, "u", level=str(level), number=number))
                ).values
                v = decode_grib_message(
                    _fetch_record(self.client, data_url, _ecmwf_record(records, "v", level=str(level), number=number))
                ).values
                winds[level]["u"].append(u)
                winds[level]["v"].append(v)
                # Apply one model-neutral derivative. IFS publishes native
                # spectral vorticity whereas AIFS and GFS do not; sampling
                # that 0.25-degree diagnostic directly onto the 1-degree
                # atlas grid retained grid-scale variance and made IFS look
                # artificially speckled. Deriving from the already resampled
                # winds keeps the tracker and map comparable across models.
                vort = relative_vorticity_x1e5(u, v)
                vorticity[level].append(vort.astype(np.float32))
            u10_values.append(
                decode_grib_message(
                    _fetch_record(self.client, data_url, _ecmwf_record(records, "10u", number=number))
                ).values
            )
            v10_values.append(
                decode_grib_message(
                    _fetch_record(self.client, data_url, _ecmwf_record(records, "10v", number=number))
                ).values
            )
            if int(step) == 0:
                precip = np.zeros((GRID_LATS.size, GRID_LONS.size), dtype=np.float32)
            else:
                precip = to_precip_mm(
                    decode_grib_message(_fetch_record(self.client, data_url, _ecmwf_record(records, "tp", number=number)))
                )
            precipitation.append(precip.astype(np.float32))
        role = "deterministic" if member == "det" else ("control" if member == "c00" else "perturbed")
        cumulative_precipitation = np.maximum.accumulate(np.stack(precipitation), axis=0)
        tracking = track_forecast_member(
            cycle=cycle,
            steps=steps,
            member=member,
            role=role,
            mslp_hpa=np.stack(mslp),
            vorticity_by_level={level: np.stack(values) for level, values in vorticity.items()},
            wind_by_level={
                level: (np.stack(values["u"]), np.stack(values["v"]))
                for level, values in winds.items()
            },
            wind_10m=(np.stack(u10_values), np.stack(v10_values)),
            precipitation_cumulative_mm=cumulative_precipitation,
        )
        return {
            "member": member,
            "tracks": tracking.tracks,
            "vorticity": np.stack(vorticity[850]),
            "precipitation": cumulative_precipitation,
            "tracking_qa": {
                "member": member,
                "detector_candidates": tracking.detector_candidates,
                "linker": tracking.linker_summary,
                "crosscheck": tracking.qa_crosscheck,
            },
        }

    def build(self, requested: str, steps: Sequence[int], member_limit: int | None = None) -> dict[str, Any]:
        cycle = self.resolve_cycle(requested, int(max(steps)))
        members = self._member_ids(member_limit)
        indexes = self._indexes_for_steps(cycle, steps)
        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(members))) as executor:
            future_map = {
                executor.submit(self._load_member, cycle, steps, member, indexes): member for member in members
            }
            for future in as_completed(future_map):
                member = future_map[future]
                try:
                    results.append(future.result())
                except Exception as error:
                    warnings.append(f"{member} unavailable: {error}")
        results.sort(key=lambda item: members.index(item["member"]))
        minimum = (
            1
            if self.definition.kind == "deterministic" or member_limit is not None
            else max(3, math.ceil(len(members) * 0.7))
        )
        if len(results) < minimum:
            LOGGER.error("%s member failures: %s", self.definition.label, "; ".join(warnings[:5]))
            raise DownloadError(f"Only {len(results)}/{len(members)} {self.definition.label} members completed")
        tracks = [track for result in results for track in result["tracks"]]
        vort_mean = np.mean(np.stack([result["vorticity"] for result in results]), axis=0)
        precip_mean = np.mean(np.stack([result["precipitation"] for result in results]), axis=0)
        return self._payload(
            cycle,
            steps,
            tracks,
            [result["member"] for result in results],
            vort_mean,
            precip_mean,
            warnings,
            [result["tracking_qa"] for result in results],
        )


def adapter_for(model: str, *, workers: int = 16, archive_root: str | None = None) -> BaseAdapter:
    if model == "ukmo-global":
        return BadcUkmoAdapter(root=archive_root, workers=workers)
    if model == "mogreps-g":
        if archive_root:
            raise ValueError("MOGREPS-G uses the rolling Met Office AWS archive and does not accept archive_root")
        return MogrepsAdapter(workers=workers)
    if model == "tigge-ecmwf":
        if archive_root:
            raise ValueError("ECMWF historical retrieval selects WeatherBench/ECDS automatically and does not accept archive_root")
        return TiggeEcmwfHybridAdapter(workers=workers)
    if model == "tigge-ncep":
        if archive_root:
            raise ValueError("NCEP historical retrieval selects NOAA/ECDS automatically and does not accept archive_root")
        return TiggeNcepAdapter(workers=workers)
    if model in TIGGE_CENTRES:
        if archive_root:
            raise ValueError("TIGGE retrieval uses ECDS and does not accept archive_root")
        return TiggeAdapter(model, workers=workers)
    if model in {"gfs", "gefs", "gefs-control", "aigfs", "aigefs"}:
        return NcepAdapter(model, workers=workers, archive_root=archive_root)
    if model == "graphcast-noaa":
        if archive_root:
            raise ValueError("NOAA/CIRA GraphCast uses its fixed public archive endpoint")
        return NoaaGraphCastAdapter(workers=workers)
    if model == "ifs":
        if archive_root:
            raise ValueError("IFS historical retrieval selects WeatherBench/Open Data automatically and does not accept archive_root")
        return EcmwfHresHybridAdapter(workers=workers)
    if model in {"ifs-ens", "aifs", "aifs-ens"}:
        if archive_root:
            raise ValueError("ECMWF Open Data is a rolling real-time feed; archive_root is unsupported")
        return EcmwfAdapter(model, workers=workers)
    raise ValueError(f"Unknown model {model!r}")
