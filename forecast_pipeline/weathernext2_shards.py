#!/usr/bin/env python3
"""Build WeatherNext 2 cycles as resumable, independent ensemble-member jobs."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from .analysis_history import analysis_entry
from .archive import AtlasVerifier, archive_manifest_entry, archive_payload
from .forecast_core import (
    MAX_PUBLISHED_TRACK_SPEED_KMH,
    atomic_write_json,
    atomic_write_json_gz,
    compact_track_payload,
    haversine_km,
    iso_z,
    manifest_entry_horizon_hours,
    parse_cycle,
    track_sidecar_url,
    utc_now,
)
from .sources import MODEL_DEFINITIONS, WeatherNext2Adapter, available_forecast_steps
from .update import latest_entry, read_manifest


MINIMUM_OPERATIONAL_MEMBERS = 45


def _json_bytes(value: Any) -> np.ndarray:
    return np.frombuffer(
        json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        dtype=np.uint8,
    )


def _from_json_bytes(value: np.ndarray) -> Any:
    return json.loads(np.asarray(value, dtype=np.uint8).tobytes().decode("utf-8"))


def build_member(cycle_text: str, member: str, output: Path) -> None:
    cycle = parse_cycle(cycle_text)
    steps = available_forecast_steps("weathernext2", cycle)
    adapter = WeatherNext2Adapter(workers=1)
    if not adapter.cycle_complete(cycle, int(steps[-1])):
        raise RuntimeError(
            f"WeatherNext 2 {cycle_text} is not complete or Google credentials lack access"
        )
    result = adapter.load_member(cycle, steps, member)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + f".part-{os.getpid()}")
    with temporary.open("wb") as stream:
        np.savez_compressed(
            stream,
            cycle=np.asarray(cycle_text),
            member=np.asarray(member),
            steps=np.asarray(steps, dtype=np.int16),
            vorticity=np.asarray(result["vorticity"], dtype=np.float32),
            precipitation=np.asarray(result["precipitation"], dtype=np.float32),
            tracks=_json_bytes(result["tracks"]),
            tracking_qa=_json_bytes(result["tracking_qa"]),
        )
    os.replace(temporary, output)


def read_member(path: Path, cycle_text: str) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as values:
        if str(values["cycle"]) != cycle_text:
            raise ValueError(
                f"{path} belongs to WeatherNext 2 {values['cycle']}, not {cycle_text}"
            )
        return {
            "member": str(values["member"]),
            "steps": values["steps"].astype(int).tolist(),
            "vorticity": np.asarray(values["vorticity"], dtype=np.float32),
            "precipitation": np.asarray(values["precipitation"], dtype=np.float32),
            "tracks": _from_json_bytes(values["tracks"]),
            "tracking_qa": _from_json_bytes(values["tracking_qa"]),
        }


def _maximum_track_speed(result: dict[str, Any]) -> float:
    maximum = 0.0
    for track in result["tracks"]:
        points = track.get("points", [])
        for previous, current in zip(points, points[1:]):
            elapsed = int(current[0]) - int(previous[0])
            if elapsed <= 0:
                continue
            maximum = max(
                maximum,
                haversine_km(
                    (float(previous[2]), float(previous[1])),
                    (float(current[2]), float(current[1])),
                )
                / elapsed,
            )
    return maximum


def combined_payload(cycle_text: str, member_paths: list[Path]) -> dict[str, Any]:
    by_member = {
        result["member"]: result
        for result in (read_member(path, cycle_text) for path in member_paths)
    }
    results = [
        by_member[key]
        for key in sorted(by_member, key=lambda value: int(value[1:]))
    ]
    geometry_rejections = []
    retained = []
    for result in results:
        speed = _maximum_track_speed(result)
        if speed > MAX_PUBLISHED_TRACK_SPEED_KMH:
            geometry_rejections.append(
                f"{result['member']} ({speed:.1f} km h-1 maximum track jump)"
            )
        else:
            retained.append(result)
    results = retained
    if len(results) < MINIMUM_OPERATIONAL_MEMBERS:
        reason = (
            f"; rejected track geometry: {', '.join(geometry_rejections)}"
            if geometry_rejections
            else ""
        )
        raise RuntimeError(
            f"only {len(results)}/64 WeatherNext 2 members completed cleanly; "
            f"{MINIMUM_OPERATIONAL_MEMBERS} are required{reason}"
        )
    steps = results[0]["steps"]
    if any(result["steps"] != steps for result in results):
        raise ValueError("WeatherNext 2 member lead axes differ")
    missing = WeatherNext2Adapter.MEMBER_COUNT - len(results)
    warnings = [f"{missing} of 64 WeatherNext 2 member shards did not complete"] if missing else []
    if geometry_rejections:
        warnings.append(
            "members excluded by the published-track motion QA: "
            + "; ".join(geometry_rejections)
        )
    adapter = WeatherNext2Adapter(workers=1)
    return adapter.payload_from_results(
        parse_cycle(cycle_text), steps, results, warnings
    )


def missing_recent_cycles(
    manifest: dict[str, Any], newest, hours: int = 72
) -> list[str]:
    """Return incomplete WeatherNext 2 cycles in the rolling live window."""

    available: dict[str, int] = {}
    for entry in manifest.get("recent", {}).get("weathernext2", []):
        cycle = str(entry.get("cycle", ""))
        available[cycle] = max(
            available.get(cycle, -1), manifest_entry_horizon_hours(entry)
        )
    for entry in manifest.get("archive", []):
        if entry.get("model") != "weathernext2":
            continue
        cycle = str(entry.get("cycle", ""))
        available[cycle] = max(
            available.get(cycle, -1), manifest_entry_horizon_hours(entry)
        )
    output = []
    for offset in range(0, hours + 1, 6):
        cycle = newest - timedelta(hours=offset)
        cycle_text = cycle.strftime("%Y%m%d%H")
        required = int(available_forecast_steps("weathernext2", cycle)[-1])
        if available.get(cycle_text, -1) < required:
            output.append(cycle_text)
    return output


def write_staging(payload: dict[str, Any], output: Path, atlas_core: Path) -> None:
    cycle = str(payload["cycle"])
    model = "weathernext2"
    cycle_url = f"cycles/{model}/{cycle}.json.gz"
    cycle_tracks_url = track_sidecar_url(cycle_url)
    archive_url = f"archive/{model}/{cycle}.json.gz"
    archive_tracks_url = track_sidecar_url(archive_url)
    atomic_write_json_gz(output / cycle_url, payload)
    atomic_write_json_gz(output / cycle_tracks_url, compact_track_payload(payload))
    archived = archive_payload(payload, AtlasVerifier(atlas_core), include_weather=False)
    atomic_write_json_gz(output / archive_url, archived)
    atomic_write_json_gz(output / archive_tracks_url, compact_track_payload(archived))
    current = latest_entry(payload, cycle_url, cycle_tracks_url)
    definition = MODEL_DEFINITIONS[model]
    manifest = {
        "schema": "mla-forecast-manifest-v1",
        "generated_utc": iso_z(utc_now()),
        "latest": {model: current},
        "recent": {model: [current]},
        "archive": [archive_manifest_entry(archived, archive_url, archive_tracks_url)],
        "tigge_archive": [],
        # WeatherNext 2 begins at +6 h, so this entry is intentionally empty
        # rather than presenting a forecast frame as an analysis.
        "analysis_history": {model: [analysis_entry(payload)]},
        "attempts": {
            model: {
                "status": "success",
                "attempted_utc": iso_z(utc_now()),
                "cycle": cycle,
                "message": "member-parallel derived-track assets written",
            }
        },
        "models": [asdict(definition)],
    }
    atomic_write_json(output / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    member = subparsers.add_parser("member")
    member.add_argument("--cycle", required=True)
    member.add_argument("--member", required=True)
    member.add_argument("--output", type=Path, required=True)
    combine = subparsers.add_parser("combine")
    combine.add_argument("--cycle", required=True)
    combine.add_argument("--members", type=Path, required=True)
    combine.add_argument("--output-root", type=Path, required=True)
    combine.add_argument("--atlas-core", type=Path, required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--hours", type=int, default=72)
    args = parser.parse_args()
    if args.command == "member":
        build_member(args.cycle, args.member, args.output)
        print(args.output)
        return
    if args.command == "plan":
        adapter = WeatherNext2Adapter(workers=1)
        newest = adapter.resolve_cycle(
            "latest", int(available_forecast_steps("weathernext2", utc_now())[-1])
        )
        manifest = read_manifest(args.manifest)
        published = manifest.get("latest", {}).get("weathernext2")
        if published and published.get("cycle"):
            newest = max(newest, parse_cycle(str(published["cycle"])))
        print(",".join(missing_recent_cycles(manifest, newest, args.hours)))
        return
    paths = sorted(args.members.glob("m??.npz"))
    payload = combined_payload(args.cycle, paths)
    write_staging(payload, args.output_root, args.atlas_core)
    print(args.output_root / "manifest.json")


if __name__ == "__main__":
    main()
