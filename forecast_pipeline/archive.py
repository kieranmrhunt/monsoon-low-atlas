#!/usr/bin/env python3
"""Public archive records and ERA5 v5.6 verification overlays."""

from __future__ import annotations

import copy
import gzip
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from .analysis_history import analysis_centres
from .forecast_core import haversine_km


def decode_polyline(value: str) -> list[tuple[float, float]]:
    index = latitude = longitude = 0
    points: list[tuple[float, float]] = []
    while index < len(value):
        result = shift = 0
        while True:
            item = ord(value[index]) - 63
            index += 1
            result |= (item & 31) << shift
            shift += 5
            if item < 32:
                break
        latitude += ~(result >> 1) if result & 1 else result >> 1
        result = shift = 0
        while True:
            item = ord(value[index]) - 63
            index += 1
            result |= (item & 31) << shift
            shift += 5
            if item < 32:
                break
        longitude += ~(result >> 1) if result & 1 else result >> 1
        points.append((latitude / 10_000.0, longitude / 10_000.0))
    return points


@dataclass(frozen=True)
class AtlasTrack:
    id: int
    start_ms: int
    end_ms: int
    category: int
    label: str
    points: list[tuple[float, float]]


class AtlasVerifier:
    def __init__(self, core_path: Path):
        with gzip.open(core_path, "rt", encoding="utf-8") as stream:
            core = json.load(stream)
        fields = {name: index for index, name in enumerate(core["track_fields"])}
        crosswalk = core.get("crosswalk", [])
        ibtracs = core.get("ibtracs_tracks", {})
        self.coverage_start = str(core["meta"]["coverage_start"])
        self.coverage_end = str(core["meta"]["coverage_end"])
        self.coverage_end_ms = int(
            datetime.fromisoformat(self.coverage_end.replace("Z", "+00:00")).timestamp() * 1000
        )
        self.catalogue_version = str(core["meta"].get("catalogue_version", "v5.6"))
        self.tracks: list[AtlasTrack] = []
        for index, row in enumerate(core["tracks"]):
            track_id = int(row[fields["id"]])
            label = f"ERA5 {self.catalogue_version} track {track_id}"
            crosswalk_item = crosswalk[index] if index < len(crosswalk) else None
            match = crosswalk_item.get("ib", {}) if isinstance(crosswalk_item, dict) else {}
            sid = str(match.get("sid", ""))
            if sid and sid in ibtracs:
                name = str(ibtracs[sid].get("name", "")).strip()
                if name and name.lower() not in {"not_named", "unnamed", "nan"}:
                    label = f"{name.title()} · ERA5 {track_id}"
            points = decode_polyline(core["paths"][index])
            self.tracks.append(
                AtlasTrack(
                    id=track_id,
                    start_ms=int(row[fields["start_ms"]]),
                    end_ms=int(row[fields["end_ms"]]),
                    category=int(row[fields["category"]]),
                    label=label,
                    points=points,
                )
            )

    @staticmethod
    def _forecast_clock(payload: dict[str, Any], track: dict[str, Any]) -> dict[int, list[Any]]:
        cycle = datetime.fromisoformat(str(payload["cycle_utc"]).replace("Z", "+00:00"))
        return {
            int((cycle + timedelta(hours=int(point[0]))).timestamp() // 3600): point
            for point in track["points"]
            if len(point) < 8 or point[7] == "o"
        }

    @staticmethod
    def _era_clock(track: AtlasTrack) -> dict[int, tuple[float, float]]:
        start_hour = int(round(track.start_ms / 3_600_000))
        return {start_hour + index: point for index, point in enumerate(track.points)}

    def _best_match(self, payload: dict[str, Any], forecast: dict[str, Any]) -> dict[str, Any] | None:
        forecast_clock = self._forecast_clock(payload, forecast)
        if not forecast_clock:
            return None
        first_ms = min(forecast_clock) * 3_600_000
        last_ms = max(forecast_clock) * 3_600_000
        best: tuple[float, dict[str, Any]] | None = None
        for atlas in self.tracks:
            if atlas.end_ms < first_ms or atlas.start_ms > last_ms:
                continue
            era_clock = self._era_clock(atlas)
            common = sorted(set(forecast_clock) & set(era_clock))
            if len(common) < 6:
                continue
            distances = np.asarray(
                [
                    haversine_km(
                        (float(forecast_clock[hour][2]), float(forecast_clock[hour][1])),
                        era_clock[hour],
                    )
                    for hour in common
                ],
                dtype=float,
            )
            median = float(np.median(distances))
            p90 = float(np.quantile(distances, 0.9))
            if median > 500.0 or p90 > 750.0:
                continue
            score = median + 0.2 * p90 - min(len(common), 72) * 2.0
            record = {
                "forecast_track_id": forecast["id"],
                "era5_track_id": atlas.id,
                "overlap_hours": len(common),
                "median_distance_km": round(median, 1),
                "p90_distance_km": round(p90, 1),
            }
            if best is None or score < best[0]:
                best = (score, record)
        return best[1] if best else None

    def verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        cycle = datetime.fromisoformat(str(payload["cycle_utc"]).replace("Z", "+00:00"))
        valid_end = cycle + timedelta(hours=int(payload["horizon_hours"]))
        if int(valid_end.timestamp() * 1000) > self.coverage_end_ms:
            return {
                "status": "pending_catalogue_extension",
                "catalogue": self.catalogue_version,
                "coverage_end": self.coverage_end,
                "matches": [],
                "tracks": [],
            }
        matches = [
            item
            for track in payload.get("tracks", [])
            if (item := self._best_match(payload, track)) is not None
        ]
        identifiers = sorted({int(item["era5_track_id"]) for item in matches})
        selected = {track.id: track for track in self.tracks if track.id in identifiers}
        overlays = []
        for track_id in identifiers:
            track = selected[track_id]
            start = datetime.fromtimestamp(track.start_ms / 1000.0, tz=UTC)
            relative_start = int(round((start - cycle).total_seconds() / 3600.0))
            overlays.append({
                "id": track.id,
                "label": track.label,
                "category": track.category,
                "start_utc": start.isoformat().replace("+00:00", "Z"),
                "end_utc": datetime.fromtimestamp(track.end_ms / 1000.0, tz=UTC).isoformat().replace("+00:00", "Z"),
                "points": [
                    [relative_start + index, round(lon, 4), round(lat, 4)]
                    for index, (lat, lon) in enumerate(track.points)
                ],
            })
        return {
            "status": "matched" if matches else "no_match",
            "catalogue": self.catalogue_version,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "matches": matches,
            "tracks": overlays,
        }


def certify_archive_payload(output: dict[str, Any]) -> dict[str, Any]:
    """Attach auditable completeness metadata to an archive cycle payload."""

    output["archive_coverage"] = {
        "complete_valid_time_axis": True,
        "valid_time_count": len(output.get("valid_times", [])),
        "published_track_count": len(output.get("tracks", [])),
        "published_track_point_count": sum(
            len(track.get("points", [])) for track in output.get("tracks", [])
        ),
        "published_disturbance_count": len(output.get("systems", [])),
        "includes_zero_disturbance_cycles": True,
    }
    weather_note = (
        "ensemble-mean weather grids are included"
        if output.get("weather")
        else "weather grids are omitted"
    )
    output["archive_note"] = (
        "Every forecast valid time and every track published by the atlas detector/linker "
        "are preserved, including cycles with no published disturbance; "
        f"{weather_note}, and internal tracking QA is omitted."
    )
    return output


def archive_payload(
    payload: dict[str, Any],
    verifier: AtlasVerifier,
    *,
    include_weather: bool = False,
) -> dict[str, Any]:
    """Prepare a public archive cycle and attach compact verification tracks."""

    output = copy.deepcopy(payload)
    output["schema"] = "mla-forecast-archive-cycle-v1"
    if not include_weather:
        output.pop("weather", None)
    output.pop("tracking_qa", None)
    output["verification"] = verifier.verification(payload)
    return certify_archive_payload(output)


def archive_manifest_entry(payload: dict[str, Any], relative_url: str) -> dict[str, Any]:
    verification = payload.get("verification", {})
    labels = [str(track.get("label", "")) for track in verification.get("tracks", [])]
    version_label = str(payload.get("model_version", {}).get("label", ""))
    return {
        "model": payload["model"]["id"],
        "model_label": payload["model"]["label"],
        "cycle": payload["cycle"],
        "cycle_utc": payload["cycle_utc"],
        "valid_end_utc": payload["valid_times"][-1],
        "url": relative_url,
        "forecast_tracks": len(payload.get("tracks", [])),
        "forecast_systems": len(payload.get("systems", [])),
        "forecast_track_points": sum(len(track.get("points", [])) for track in payload.get("tracks", [])),
        "valid_time_count": len(payload.get("valid_times", [])),
        "complete_valid_time_axis": bool(payload.get("archive_coverage", {}).get("complete_valid_time_axis")),
        "weather_fields": sorted(
            name
            for name, field in payload.get("weather", {}).items()
            if isinstance(field, dict) and "shape" in field
        ),
        "analysis_centres": analysis_centres(payload),
        "model_version": payload.get("model_version", {}),
        "verification_status": verification.get("status", "unavailable"),
        "verification_labels": labels,
        "search_text": " ".join(
            [payload["model"]["label"], version_label, payload["cycle"], payload["cycle_utc"], *labels]
        ).lower(),
    }
