"""Compact t+0 centres and forecast signatures used to stitch forecast tracks."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any


ANALYSIS_HISTORY_HOURS = 14 * 24
SIGNATURE_STEP_HOURS = 6
SIGNATURE_MAX_HOURS = 60


def _system_tracks(payload: dict[str, Any], system: dict[str, Any]) -> list[dict[str, Any]]:
    track_ids = {str(value) for value in system.get("track_ids", [])}
    return [track for track in payload.get("tracks", []) if str(track.get("id")) in track_ids]


def _mean_points(
    payload: dict[str, Any], system: dict[str, Any]
) -> list[list[float | int]]:
    tracks = _system_tracks(payload, system)
    by_step: dict[int, list[list[Any]]] = {}
    for track in tracks:
        for point in track.get("points", []):
            if len(point) < 3:
                continue
            step = int(point[0])
            if step < 0 or step > SIGNATURE_MAX_HOURS or step % SIGNATURE_STEP_HOURS:
                continue
            by_step.setdefault(step, []).append(point)

    minimum = max(1, math.ceil(int(system.get("member_count") or len(tracks)) * 0.2))
    output: list[list[float | int]] = []
    for step, points in sorted(by_step.items()):
        if len(points) < minimum:
            continue
        output.append([
            step,
            round(sum(float(point[1]) for point in points) / len(points), 3),
            round(sum(float(point[2]) for point in points) / len(points), 3),
        ])
    return output


def analysis_centres(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return t+0 centres plus a small six-hourly signature for safe matching."""

    output: list[dict[str, Any]] = []
    for system in payload.get("systems", []):
        points = _mean_points(payload, system)
        initial = next((point for point in points if int(point[0]) == 0), None)
        if initial is None:
            # A forecast-only genesis has no analysed centre to append to history.
            continue
        tracks = _system_tracks(payload, system)
        output.append({
            "system_id": str(system.get("id", "")),
            "longitude": initial[1],
            "latitude": initial[2],
            "member_count": int(system.get("member_count") or len(tracks)),
            "peak_category": max(
                (int(track.get("maximum_provisional_category") or 0) for track in tracks),
                default=0,
            ),
            "match_points": points,
        })
    return output


def analysis_entry(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle": payload["cycle"],
        "cycle_utc": payload["cycle_utc"],
        "model_version": payload.get("model_version", {}),
        "centres": analysis_centres(payload),
    }


def replace_analysis_entry(
    entries: list[dict[str, Any]],
    new: dict[str, Any],
    *,
    window_hours: int = ANALYSIS_HISTORY_HOURS,
) -> list[dict[str, Any]]:
    """De-duplicate and retain compact analysis entries near the newest cycle."""

    combined = [
        item for item in entries
        if str(item.get("cycle", "")) != str(new.get("cycle", ""))
    ] + [new]
    combined.sort(key=lambda item: str(item.get("cycle", "")), reverse=True)
    newest = datetime.fromisoformat(str(combined[0]["cycle_utc"]).replace("Z", "+00:00"))
    retained = []
    for item in combined:
        cycle = datetime.fromisoformat(str(item["cycle_utc"]).replace("Z", "+00:00"))
        if 0 <= (newest - cycle).total_seconds() <= window_hours * 3600:
            retained.append(item)
    return retained
