#!/usr/bin/env python3
"""Build AIGEFS cycles as independent member jobs and combine them safely."""

from __future__ import annotations

import argparse
import gzip
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
    atomic_write_json,
    atomic_write_json_gz,
    compact_track_payload,
    iso_z,
    manifest_entry_horizon_hours,
    parse_cycle,
    track_sidecar_url,
    utc_now,
)
from .sources import MODEL_DEFINITIONS, NcepAdapter, available_forecast_steps
from .update import latest_entry, read_manifest


def _json_bytes(value: Any) -> np.ndarray:
    return np.frombuffer(
        json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        dtype=np.uint8,
    )


def _from_json_bytes(value: np.ndarray) -> Any:
    return json.loads(np.asarray(value, dtype=np.uint8).tobytes().decode("utf-8"))


def build_member(cycle_text: str, member: str, output: Path) -> None:
    if not member.startswith("p") or not member[1:].isdigit() or not 1 <= int(member[1:]) <= 31:
        raise ValueError(f"invalid AIGEFS member {member!r}")
    cycle = parse_cycle(cycle_text)
    steps = available_forecast_steps("aigefs", cycle)
    adapter = NcepAdapter("aigefs", workers=1)
    if not adapter.cycle_complete(cycle, int(steps[-1])):
        raise RuntimeError(f"AIGEFS {cycle_text} is no longer complete at NOMADS")
    result = adapter._load_member(cycle, steps, member)
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
            source_gap_steps=np.asarray(result.get("source_gap_steps", []), dtype=np.int16),
        )
    os.replace(temporary, output)


def read_member(path: Path, cycle_text: str) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as values:
        if str(values["cycle"]) != cycle_text:
            raise ValueError(f"{path} belongs to AIGEFS {values['cycle']}, not {cycle_text}")
        return {
            "member": str(values["member"]),
            "steps": values["steps"].astype(int).tolist(),
            "vorticity": np.asarray(values["vorticity"], dtype=np.float32),
            "precipitation": np.asarray(values["precipitation"], dtype=np.float32),
            "tracks": _from_json_bytes(values["tracks"]),
            "tracking_qa": _from_json_bytes(values["tracking_qa"]),
            "source_gap_steps": values["source_gap_steps"].astype(int).tolist(),
        }


def combined_payload(cycle_text: str, member_paths: list[Path]) -> dict[str, Any]:
    results = [read_member(path, cycle_text) for path in member_paths]
    by_member = {result["member"]: result for result in results}
    results = [by_member[key] for key in sorted(by_member, key=lambda value: int(value[1:]))]
    if len(results) < 22:
        raise RuntimeError(f"only {len(results)}/31 AIGEFS members completed; 22 are required")
    steps = results[0]["steps"]
    if any(result["steps"] != steps for result in results):
        raise ValueError("AIGEFS member lead axes differ")
    warnings = [f"{31 - len(results)} of 31 AIGEFS members were unavailable"] if len(results) < 31 else []
    reconstructed = {
        result["member"]: result["source_gap_steps"]
        for result in results if result["source_gap_steps"]
    }
    if reconstructed:
        warnings.append(
            "isolated NOAA source gaps reconstructed by linear temporal interpolation: "
            + "; ".join(f"{member} at {', '.join(f'+{step} h' for step in member_steps)}" for member, member_steps in reconstructed.items())
        )
    adapter = NcepAdapter("aigefs", workers=1)
    payload = adapter._payload(
        parse_cycle(cycle_text),
        steps,
        [track for result in results for track in result["tracks"]],
        [result["member"] for result in results],
        np.mean(np.stack([result["vorticity"] for result in results]), axis=0),
        np.mean(np.stack([result["precipitation"] for result in results]), axis=0),
        warnings,
        [result["tracking_qa"] for result in results],
        expected_members=31,
    )
    payload["source"]["retrieval"] = "member-parallel NOMADS inventory byte ranges; atlas domain resampled to 1 degree"
    if reconstructed:
        payload["source"]["gap_reconstruction"] = {
            "policy": "linear interpolation of isolated missing six-hour member frames bounded by source-present neighbours",
            "members": reconstructed,
            "reconstructed_member_frames": sum(len(values) for values in reconstructed.values()),
        }
    return payload


def missing_recent_cycles(
    manifest: dict[str, Any], newest, hours: int = 72
) -> list[str]:
    """Return incomplete AIGEFS cycles in the rolling operational window."""

    available: dict[str, int] = {}
    for entry in manifest.get("recent", {}).get("aigefs", []):
        cycle = str(entry.get("cycle", ""))
        available[cycle] = max(
            available.get(cycle, -1), manifest_entry_horizon_hours(entry)
        )
    for entry in manifest.get("archive", []):
        if entry.get("model") != "aigefs":
            continue
        cycle = str(entry.get("cycle", ""))
        available[cycle] = max(
            available.get(cycle, -1), manifest_entry_horizon_hours(entry)
        )
    output = []
    for offset in range(0, hours + 1, 6):
        cycle = newest - timedelta(hours=offset)
        cycle_text = cycle.strftime("%Y%m%d%H")
        required = int(available_forecast_steps("aigefs", cycle)[-1])
        if available.get(cycle_text, -1) < required:
            output.append(cycle_text)
    return output


def write_staging(payload: dict[str, Any], output: Path, atlas_core: Path) -> None:
    cycle = str(payload["cycle"])
    model = "aigefs"
    cycle_url = f"cycles/{model}/{cycle}.json.gz"
    cycle_tracks_url = track_sidecar_url(cycle_url)
    archive_url = f"archive/{model}/{cycle}.json.gz"
    archive_tracks_url = track_sidecar_url(archive_url)
    atomic_write_json_gz(output / cycle_url, payload)
    atomic_write_json_gz(output / cycle_tracks_url, compact_track_payload(payload))
    archived = archive_payload(payload, AtlasVerifier(atlas_core), include_weather=True)
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
        "analysis_history": {model: [analysis_entry(payload)]},
        "attempts": {model: {"status": "success", "attempted_utc": iso_z(utc_now()), "cycle": cycle, "message": "member-parallel cycle assets written"}},
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
        newest = NcepAdapter("aigefs", workers=1).resolve_cycle(
            "latest", int(available_forecast_steps("aigefs", utc_now())[-1])
        )
        manifest = read_manifest(args.manifest)
        published = manifest.get("latest", {}).get("aigefs")
        if published and published.get("cycle"):
            newest = max(newest, parse_cycle(str(published["cycle"])))
        print(",".join(missing_recent_cycles(manifest, newest, args.hours)))
        return
    paths = sorted(args.members.glob("p*.npz"))
    payload = combined_payload(args.cycle, paths)
    write_staging(payload, args.output_root, args.atlas_core)
    print(args.output_root / "manifest.json")


if __name__ == "__main__":
    main()
