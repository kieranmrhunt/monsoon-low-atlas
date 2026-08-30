#!/usr/bin/env python3
"""Official forecast-source adapters used by the operational atlas updater."""

from __future__ import annotations

import json
import logging
import math
import re
import tempfile
import time
import urllib.error
import urllib.request
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


def available_forecast_steps(model: str, cycle: datetime) -> list[int]:
    """Return every six-hourly lead supplied by the selected forecast stream."""

    value = cycle if cycle.tzinfo is not None else cycle.replace(tzinfo=UTC)
    if model in {"gfs", "gefs", "gefs-control"}:
        horizon = 384
    elif model in {"ifs", "ifs-ens"}:
        horizon = 360 if value.hour in {0, 12} else 144
    elif model in {"aifs", "aifs-ens", "tigge-ecmwf"}:
        horizon = 360
    elif model == "ukmo-global":
        horizon = 144
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
    "tigge-ecmwf": ModelDefinition(
        "tigge-ecmwf", "ECMWF TIGGE ENS", "ECMWF", "ensemble", 51,
        "Historical ECMWF control plus perturbed ensemble from the TIGGE archive",
        "https://ecds.ecmwf.int/datasets/tigge-forecasts", "ECMWF ECDS TIGGE archive", "CC BY 4.0", "#73539b",
    ),
}

DEFAULT_MODELS = ("gfs", "gefs", "ifs", "ifs-ens", "aifs", "aifs-ens")


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
                    self._field_path(cycle, field_name, area, horizon)
            for field_name in ("accumulated_dynamic_rain", "accumulated_convective_rain"):
                for area in self.AREAS:
                    self._field_path(cycle, field_name, area, horizon)
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


class TiggeEcmwfAdapter(BaseAdapter):
    """Historical ECMWF ensembles retrieved from the ECDS TIGGE archive."""

    ECDS_URL = "https://ecds.ecmwf.int/api"
    DATASET = "tigge-forecasts"
    ARCHIVE_START = datetime(2006, 10, 1, 0, tzinfo=UTC)

    def __init__(self, workers: int = 8):
        super().__init__(workers=workers)
        self.definition = MODEL_DEFINITIONS["tigge-ecmwf"]

    def resolve_cycle(self, requested: str, horizon: int) -> datetime:
        if requested == "latest":
            raise DownloadError("TIGGE is a delayed historical archive and requires an explicit cycle")
        value = parse_cycle(requested)
        if not self.cycle_complete(value, horizon):
            raise DownloadError(f"ECMWF TIGGE cycle {requested} is outside the supported archive/cadence")
        return value

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        if cycle.tzinfo is None:
            cycle = cycle.replace(tzinfo=UTC)
        return cycle >= self.ARCHIVE_START and cycle.hour in {0, 12} and 0 <= horizon <= 360

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
            "origin": "ecmf",
            "param": "131/132" if levtype == "pl" else "151/165/166/228",
            "step": "/".join(str(int(step)) for step in steps),
            "time": cycle.strftime("%H:00:00"),
            "type": forecast_type,
        }
        if levtype == "pl":
            request["levelist"] = "500/700/850"
        client = cdsapi.Client(url=self.ECDS_URL, key=self._credentials(), quiet=True)
        client.retrieve(self.DATASET, request, str(target))

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
                        number = int(codes_get(handle, "perturbationNumber"))
                        member = "c00" if forecast_type == "cf" else f"p{number:02d}"
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
        return sorted(values, key=lambda value: (value != "c00", int(value[1:])))

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
        precipitation = np.maximum.accumulate(np.stack([
            to_precip_mm(fields[(member, int(step), "tp", 0)])
            for step in steps
        ]), axis=0)
        role = "control" if member == "c00" else "perturbed"
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
        with tempfile.TemporaryDirectory(prefix=f"mla-tigge-{cycle_id(cycle)}-") as directory:
            root = Path(directory)
            paths = []
            for forecast_type in ("cf", "pf"):
                for levtype in ("pl", "sfc"):
                    target = root / f"{forecast_type}-{levtype}.grib"
                    self._retrieve(cycle, steps, target, forecast_type, levtype)
                    paths.append(target)
            fields = self._read_fields(paths, cycle)

        members = self._member_ids(fields)
        if member_limit is not None:
            members = members[:max(1, member_limit)]
        if "c00" not in members:
            raise DownloadError("ECMWF TIGGE control member is missing")
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
            raise DownloadError(f"Only {len(results)}/{len(members)} ECMWF TIGGE members completed")
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
        payload["source"]["retrieval"] = "ECMWF ECDS TIGGE subset at 1 degree; all available control/perturbed members"
        return payload


class NcepAdapter(BaseAdapter):
    LIVE_GFS_ROOT = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
    LIVE_GEFS_ROOT = "https://noaa-gefs-pds.s3.amazonaws.com"
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
        if model not in {"gfs", "gefs", "gefs-control"}:
            raise ValueError(model)
        super().__init__(client, workers)
        self.definition = MODEL_DEFINITIONS[model]
        self.archive_root = archive_root

    def _urls(self, cycle: datetime, step: int, member: str = "det") -> tuple[str, str]:
        date = cycle.strftime("%Y%m%d")
        hour = cycle.strftime("%H")
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
        member = "det" if self.definition.id == "gfs" else "c00"
        unused, index_url = self._urls(cycle, horizon, member)
        try:
            text = self.client.text(index_url)
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
        if self.definition.id == "gfs":
            return ["det"]
        if self.definition.id == "gefs-control":
            return ["c00"]
        perturbed_members = 30 if cycle.replace(tzinfo=None) >= self.GEFS_V12_START else 20
        values = ["c00"] + [f"p{number:02d}" for number in range(1, perturbed_members + 1)]
        if member_limit is not None:
            return values[: max(1, member_limit)]
        return values

    def _load_member(self, cycle: datetime, steps: Sequence[int], member: str) -> dict[str, Any]:
        mslp: list[np.ndarray] = []
        winds: dict[int, dict[str, list[np.ndarray]]] = {
            level: {"u": [], "v": []} for level in (850, 700, 500)
        }
        vorticity: dict[int, list[np.ndarray]] = {level: [] for level in (850, 700, 500)}
        u10_values: list[np.ndarray] = []
        v10_values: list[np.ndarray] = []
        precipitation: list[np.ndarray] = []
        cumulative_flags: list[bool] = []
        complete_steps: list[int] = []
        errors: list[str] = []
        for step in steps:
            data_url, index_url = self._urls(cycle, int(step), member)
            try:
                records = parse_ncep_index(self.client.text(index_url))
                msl = to_mslp_hpa(
                    decode_grib_message(
                        _fetch_record(self.client, data_url, _ncep_record(records, ":PRMSL:mean sea level:"))
                    )
                )
                mslp.append(msl)
                for level in (850, 700, 500):
                    u = decode_grib_message(
                        _fetch_record(self.client, data_url, _ncep_record(records, f":UGRD:{level} mb:"))
                    ).values
                    v = decode_grib_message(
                        _fetch_record(self.client, data_url, _ncep_record(records, f":VGRD:{level} mb:"))
                    ).values
                    winds[level]["u"].append(u)
                    winds[level]["v"].append(v)
                    vorticity[level].append(relative_vorticity_x1e5(u, v))
                u10_values.append(
                    decode_grib_message(
                        _fetch_record(self.client, data_url, _ncep_record(records, ":UGRD:10 m above ground:"))
                    ).values
                )
                v10_values.append(
                    decode_grib_message(
                        _fetch_record(self.client, data_url, _ncep_record(records, ":VGRD:10 m above ground:"))
                    ).values
                )
                precip_record = _ncep_precip_record(records, int(step))
                if precip_record is None:
                    precip = np.zeros((GRID_LATS.size, GRID_LONS.size), dtype=np.float32)
                    cumulative = True
                else:
                    precip_field = decode_grib_message(_fetch_record(self.client, data_url, precip_record))
                    precip = to_precip_mm(precip_field)
                    cumulative = _precip_is_cumulative(precip_field.step_range, int(step))
                precipitation.append(precip)
                cumulative_flags.append(cumulative)
                complete_steps.append(int(step))
            except Exception as error:
                errors.append(f"+{int(step):03d} h: {error}")
                break
        if complete_steps != [int(step) for step in steps]:
            raise DownloadError(f"{member} incomplete ({len(complete_steps)}/{len(steps)} steps): {'; '.join(errors[:2])}")
        role = "deterministic" if self.definition.kind == "deterministic" else ("control" if member == "c00" else "perturbed")
        cumulative_precipitation = _finalise_precip(precipitation, cumulative_flags)
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
            expected_members=(
                1
                if self.definition.id in {"gfs", "gefs-control"}
                else 31 if cycle.replace(tzinfo=None) >= self.GEFS_V12_START else 21
            ),
        )


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
    if model == "tigge-ecmwf":
        if archive_root:
            raise ValueError("TIGGE retrieval uses ECDS and does not accept archive_root")
        return TiggeEcmwfAdapter(workers=workers)
    if model in {"gfs", "gefs", "gefs-control"}:
        return NcepAdapter(model, workers=workers, archive_root=archive_root)
    if model in {"ifs", "ifs-ens", "aifs", "aifs-ens"}:
        if archive_root:
            raise ValueError("ECMWF Open Data is a rolling real-time feed; archive_root is unsupported")
        return EcmwfAdapter(model, workers=workers)
    raise ValueError(f"Unknown model {model!r}")
