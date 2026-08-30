#!/usr/bin/env python3
"""Official forecast-source adapters used by the operational atlas updater."""

from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta, datetime
from typing import Any, Sequence

import numpy as np

from .forecast_core import (
    GRID_LATS,
    GRID_LONS,
    assign_systems,
    candidate_cycles,
    compact_weather,
    cycle_id,
    decode_grib_message,
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


LOGGER = logging.getLogger("mla.forecast.sources")
USER_AGENT = "monsoon-low-atlas-forecast/1.0 (+https://kieranmrhunt.github.io/monsoon-low-atlas/)"


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
}

DEFAULT_MODELS = tuple(MODEL_DEFINITIONS)


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
            "grid": grid_metadata(),
            "members": {
                "available": len(member_ids),
                "expected": definition.expected_members,
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
                "native_to_linker_time": "continuous fields linearly interpolated from six-hourly forecast output to the hourly linker clock",
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


class NcepAdapter(BaseAdapter):
    LIVE_GFS_ROOT = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
    LIVE_GEFS_ROOT = "https://noaa-gefs-pds.s3.amazonaws.com"

    def __init__(
        self,
        model: str,
        client: HttpClient | None = None,
        workers: int = 16,
        archive_root: str | None = None,
    ):
        if model not in {"gfs", "gefs"}:
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
            base = f"{self.LIVE_GFS_ROOT}/gfs.{date}/{hour}/atmos/gfs.t{hour}z.pgrb2.0p25.f{step:03d}"
            return base, f"{base}.idx"
        prefix = "gec00" if member == "c00" else f"ge{member}"
        base = (
            f"{self.LIVE_GEFS_ROOT}/gefs.{date}/{hour}/atmos/pgrb2ap5/"
            f"{prefix}.t{hour}z.pgrb2a.0p50.f{step:03d}"
        )
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

    def _member_ids(self, member_limit: int | None) -> list[str]:
        if self.definition.id == "gfs":
            return ["det"]
        values = ["c00"] + [f"p{number:02d}" for number in range(1, 31)]
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
        role = "deterministic" if member == "det" else ("control" if member == "c00" else "perturbed")
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
        members = self._member_ids(member_limit)
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
                try:
                    vort = decode_grib_message(
                        _fetch_record(
                            self.client,
                            data_url,
                            _ecmwf_record(records, "vo", level=str(level), number=number),
                        )
                    ).values * 1.0e5
                except DownloadError:
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
    if model in {"gfs", "gefs"}:
        return NcepAdapter(model, workers=workers, archive_root=archive_root)
    if model in {"ifs", "ifs-ens", "aifs", "aifs-ens"}:
        if archive_root:
            raise ValueError("ECMWF Open Data is a rolling real-time feed; archive_root is unsupported")
        return EcmwfAdapter(model, workers=workers)
    raise ValueError(f"Unknown model {model!r}")
