#!/usr/bin/env python3
"""Build compact, auditable forecast-track verification for the archive UI."""

from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import numpy as np

from .archive import AtlasVerifier
from .forecast_core import atomic_write_json_gz, haversine_km


SCHEMA = "mla-forecast-archive-verification-v1"
LEAD_INTERVAL_HOURS = 24
# Only collections sampled independently of observed LPS occurrence can support
# hits, misses and false alarms. Other archives remain valid for conditional
# track and lifecycle errors.
CONTINUOUS_MODELS = frozenset({"ukmo-global"})


def _read_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _quantile(values: Iterable[float], probability: float) -> float | None:
    finite = np.asarray([float(value) for value in values if math.isfinite(float(value))], dtype=float)
    return round(float(np.quantile(finite, probability)), 2) if finite.size else None


def _mean(values: Iterable[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return round(float(np.mean(finite)), 2) if finite else None


def _observed_points(track: dict[str, Any]) -> list[list[Any]]:
    return [
        point
        for point in track.get("points", [])
        if len(point) < 8 or str(point[7]).lower() == "o"
    ]


def normalized_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Enforce one ERA5 identity per forecast member and forecast track.

    New payloads already satisfy this contract. Legacy archive payloads stored
    only each track's best match, so their candidates are greedily de-duplicated
    by the same score used by the original verifier.
    """

    tracks = {str(track.get("id")): track for track in payload.get("tracks", [])}
    ranked = sorted(
        payload.get("verification", {}).get("matches", []),
        key=lambda item: (
            float(item.get("median_distance_km", math.inf))
            + 0.2 * float(item.get("p90_distance_km", math.inf))
            - min(int(item.get("overlap_hours", 0)), 72) * 2,
            str(item.get("forecast_track_id", "")),
        ),
    )
    used_forecast: set[tuple[str, str]] = set()
    used_era: set[tuple[str, int]] = set()
    output = []
    for match in ranked:
        track = tracks.get(str(match.get("forecast_track_id")))
        if not track:
            continue
        member = str(match.get("member") or track.get("member") or "det")
        forecast_key = (member, str(match.get("forecast_track_id")))
        era_key = (member, int(match.get("era5_track_id")))
        if forecast_key in used_forecast or era_key in used_era:
            continue
        used_forecast.add(forecast_key)
        used_era.add(era_key)
        output.append({**match, "member": member})
    return output


def _track_errors(
    payload: dict[str, Any],
    match: dict[str, Any],
    forecast: dict[str, Any],
    era: dict[str, Any],
) -> tuple[list[tuple[int, float]], dict[str, Any]]:
    forecast_clock = {int(point[0]): point for point in _observed_points(forecast)}
    era_clock = {int(point[0]): point for point in era.get("points", [])}
    common = sorted(set(forecast_clock) & set(era_clock))
    errors = [
        (
            step,
            haversine_km(
                (float(forecast_clock[step][2]), float(forecast_clock[step][1])),
                (float(era_clock[step][2]), float(era_clock[step][1])),
            ),
        )
        for step in common
    ]
    horizon = int(payload.get("horizon_hours", max(payload.get("steps", [0]))))
    forecast_start = min(forecast_clock) if forecast_clock else None
    forecast_end = max(forecast_clock) if forecast_clock else None
    era_start = min(era_clock) if era_clock else None
    era_end = max(era_clock) if era_clock else None
    genesis_error = (
        forecast_start - era_start
        if forecast_start is not None and era_start is not None and 0 <= era_start <= horizon
        else None
    )
    lysis_error = (
        forecast_end - era_end
        if forecast_end is not None and era_end is not None and 0 <= era_end <= horizon
        else None
    )
    forecast_category = forecast.get("maximum_provisional_category")
    era_category = era.get("category")
    return errors, {
        "model": str(payload["model"]["id"]),
        "version": str(payload.get("model_version", {}).get("label", "Version unavailable")),
        "cycle": str(payload["cycle"]),
        "era5_track_id": int(match["era5_track_id"]),
        "member": str(match["member"]),
        "median_position_error_km": round(float(median(value for _, value in errors)), 2) if errors else None,
        "genesis_error_hours": genesis_error,
        "lysis_error_hours": lysis_error,
        "peak_category_error": (
            int(forecast_category) - int(era_category)
            if forecast_category is not None and era_category is not None
            else None
        ),
    }


def _system_at_step(payload: dict[str, Any], step: int) -> dict[str, set[str]]:
    tracks = {str(track.get("id")): track for track in payload.get("tracks", [])}
    output: dict[str, set[str]] = {}
    for system in payload.get("systems", []):
        active_members = {
            str(track.get("member", "det"))
            for track_id in system.get("track_ids", [])
            if (track := tracks.get(str(track_id))) is not None
            and any(int(point[0]) == step for point in _observed_points(track))
        }
        if active_members:
            output[str(system.get("id"))] = active_members
    return output


def _occurrence_rows(
    payload: dict[str, Any],
    verifier: AtlasVerifier,
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    model = str(payload["model"]["id"])
    if model not in CONTINUOUS_MODELS or str(payload.get("model", {}).get("kind", "deterministic")) == "ensemble":
        return []
    cycle_hour = int(datetime.fromisoformat(str(payload["cycle_utc"]).replace("Z", "+00:00")).timestamp() // 3600)
    track_system = {
        str(track_id): str(system.get("id"))
        for system in payload.get("systems", [])
        for track_id in system.get("track_ids", [])
    }
    matched_systems = {
        (track_system.get(str(match["forecast_track_id"])), int(match["era5_track_id"]))
        for match in matches
        if track_system.get(str(match["forecast_track_id"]))
    }
    rows = []
    horizon = int(payload.get("horizon_hours", max(payload.get("steps", [0]))))
    for lead in range(0, horizon + 1, LEAD_INTERVAL_HOURS):
        active_systems = _system_at_step(payload, lead)
        active_era = {track.id for track in verifier.tracks_by_hour.get(cycle_hour + lead, [])}
        hits = {
            (system_id, era_id)
            for system_id, era_id in matched_systems
            if system_id in active_systems and era_id in active_era
        }
        hit_systems = {system_id for system_id, _ in hits}
        hit_era = {era_id for _, era_id in hits}
        rows.append({
            "model": model,
            "version": str(payload.get("model_version", {}).get("label", "Version unavailable")),
            "lead": lead,
            "hits": len(hits),
            "misses": len(active_era - hit_era),
            "false_alarms": len(set(active_systems) - hit_systems),
        })
    return rows


def payload_records(
    payload: dict[str, Any],
    verifier: AtlasVerifier,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if payload.get("verification", {}).get("status") == "pending_catalogue_extension":
        return [], [], []
    tracks = {str(track.get("id")): track for track in payload.get("tracks", [])}
    era_tracks = {str(track.get("id")): track for track in payload.get("verification", {}).get("tracks", [])}
    matches = normalized_matches(payload)
    position_samples: dict[tuple[str, str, str, int, int], list[tuple[float, str]]] = defaultdict(list)
    lifecycle_members = []
    for match in matches:
        forecast = tracks.get(str(match["forecast_track_id"]))
        era = era_tracks.get(str(match["era5_track_id"]))
        if not forecast or not era:
            continue
        errors, record = _track_errors(payload, match, forecast, era)
        lifecycle_members.append(record)
        for lead, error in errors:
            if lead < 0 or lead % LEAD_INTERVAL_HOURS:
                continue
            key = (record["model"], record["version"], record["cycle"], record["era5_track_id"], lead)
            position_samples[key].append((error, record["member"]))
    position = [
        {
            "model": key[0], "version": key[1], "cycle": key[2], "era5_track_id": key[3], "lead": key[4],
            "median_error_km": round(float(median(value for value, _ in values)), 2),
            "mean_error_km": round(float(np.mean([value for value, _ in values])), 2),
            "members": len({member for _, member in values}),
        }
        for key, values in position_samples.items()
    ]
    lifecycle_groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in lifecycle_members:
        lifecycle_groups[(record["model"], record["version"], record["cycle"], record["era5_track_id"])].append(record)
    lifecycle = []
    for key, values in lifecycle_groups.items():
        aggregate = lambda name: [float(value[name]) for value in values if value.get(name) is not None]
        track_error = aggregate("median_position_error_km")
        genesis = aggregate("genesis_error_hours")
        lysis = aggregate("lysis_error_hours")
        category = aggregate("peak_category_error")
        lifecycle.append({
            "model": key[0], "version": key[1], "cycle": key[2], "era5_track_id": key[3],
            "members": len({str(value["member"]) for value in values}),
            "median_position_error_km": round(float(median(track_error)), 2) if track_error else None,
            "genesis_error_hours": round(float(median(genesis)), 2) if genesis else None,
            "lysis_error_hours": round(float(median(lysis)), 2) if lysis else None,
            "peak_category_error": round(float(median(category)), 2) if category else None,
        })
    return position, lifecycle, _occurrence_rows(payload, verifier, matches)


def _position_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["model"], row["version"], int(row["lead"]))].append(row)
    return [
        {
            "model": key[0], "version": key[1], "lead": key[2], "samples": len(values),
            "events": len({int(value["era5_track_id"]) for value in values}),
            "median_error_km": _quantile((value["median_error_km"] for value in values), 0.5),
            "mean_error_km": _mean(value["mean_error_km"] for value in values),
            "p25_error_km": _quantile((value["median_error_km"] for value in values), 0.25),
            "p75_error_km": _quantile((value["median_error_km"] for value in values), 0.75),
        }
        for key, values in sorted(grouped.items())
    ]


def _lifecycle_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["model"], row["version"])].append(row)
    output = []
    for key, values in sorted(grouped.items()):
        metric = lambda name: [float(value[name]) for value in values if value.get(name) is not None]
        genesis, lysis, category = metric("genesis_error_hours"), metric("lysis_error_hours"), metric("peak_category_error")
        output.append({
            "model": key[0], "version": key[1], "matched_tracks": len(values),
            "events": len({int(value["era5_track_id"]) for value in values}),
            "median_track_error_km": _quantile(metric("median_position_error_km"), 0.5),
            "genesis_samples": len(genesis), "genesis_mae_hours": _quantile((abs(value) for value in genesis), 0.5), "genesis_bias_hours": _mean(genesis),
            "lysis_samples": len(lysis), "lysis_mae_hours": _quantile((abs(value) for value in lysis), 0.5), "lysis_bias_hours": _mean(lysis),
            "category_samples": len(category), "peak_category_bias": _mean(category),
        })
    return output


def _occurrence_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["model"], row["version"], int(row["lead"]))].append(row)
    output = []
    for key, values in sorted(grouped.items()):
        hits = sum(int(value["hits"]) for value in values)
        misses = sum(int(value["misses"]) for value in values)
        false_alarms = sum(int(value["false_alarms"]) for value in values)
        output.append({
            "model": key[0], "version": key[1], "lead": key[2], "cycles": len(values),
            "hits": hits, "misses": misses, "false_alarms": false_alarms,
            "probability_of_detection": round(hits / (hits + misses), 4) if hits + misses else None,
            "false_alarm_ratio": round(false_alarms / (hits + false_alarms), 4) if hits + false_alarms else None,
            "critical_success_index": round(hits / (hits + misses + false_alarms), 4) if hits + misses + false_alarms else None,
        })
    return output


def build_summary(root: Path, core: Path) -> dict[str, Any]:
    manifest = _read_json(root / "archive-manifest.json.gz")
    verifier = AtlasVerifier(core)
    position: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    occurrence: list[dict[str, Any]] = []
    failures = []
    entries = [*manifest.get("archive", []), *manifest.get("tigge_archive", [])]
    for index, entry in enumerate(entries, start=1):
        try:
            payload = _read_json(root / entry["url"])
            payload_position, payload_lifecycle, payload_occurrence = payload_records(payload, verifier)
            position.extend(payload_position)
            lifecycle.extend(payload_lifecycle)
            occurrence.extend(payload_occurrence)
        except Exception as error:  # retain all usable archive evidence
            failures.append({"model": entry.get("model"), "cycle": entry.get("cycle"), "error": str(error)})
        if index % 500 == 0:
            print(f"read {index}/{len(entries)} archive cycles", flush=True)
    models = {
        str(model["id"]): {key: model.get(key) for key in ("label", "kind", "colour", "centre")}
        for model in manifest.get("models", [])
    }
    for entry in entries:
        model = str(entry.get("model", ""))
        if model and model not in models:
            expected = int(entry.get("members_expected") or entry.get("members_available") or 1)
            models[model] = {
                "label": str(entry.get("model_label") or model),
                "kind": "ensemble" if expected > 1 else "deterministic",
                "colour": None,
                "centre": None,
            }
    available_models = sorted({row["model"] for row in lifecycle})
    return {
        "schema": SCHEMA,
        "generated_utc": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "catalogue": verifier.catalogue_version,
        "catalogue_coverage": [verifier.coverage_start, verifier.coverage_end],
        "lead_interval_hours": LEAD_INTERVAL_HOURS,
        "models": {model: models.get(model, {"label": model}) for model in available_models},
        "sampling": {
            "continuous_models": sorted(CONTINUOUS_MODELS & set(available_models)),
            "event_conditioned_models": sorted(set(available_models) - CONTINUOUS_MODELS),
            "occurrence_policy": "POD, FAR and CSI are published only for independently sampled continuous archives; event-conditioned collections contribute track and lifecycle errors only.",
        },
        "matching_policy": {
            "scope": "one-to-one within each deterministic run or ensemble member",
            "minimum_overlap_hours": 6,
            "maximum_median_distance_km": 500,
            "maximum_p90_distance_km": 750,
        },
        "position_summary": _position_summary(position),
        "lifecycle_summary": _lifecycle_summary(lifecycle),
        "occurrence_summary": _occurrence_summary(occurrence),
        "position_cases": position,
        "lifecycle_cases": lifecycle,
        "coverage": {
            "archive_cycles_read": len(entries) - len(failures),
            "archive_cycles_failed": len(failures),
            "position_cases": len(position),
            "matched_track_cases": len(lifecycle),
            "failures": failures[:50],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.archive_root / "verification-summary.json.gz"
    summary = build_summary(args.archive_root, args.core)
    atomic_write_json_gz(output, summary)
    print(json.dumps({"output": str(output), **summary["coverage"]}, indent=2))


if __name__ == "__main__":
    main()
