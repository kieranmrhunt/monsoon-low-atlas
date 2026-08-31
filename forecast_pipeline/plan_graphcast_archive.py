#!/usr/bin/env python3
"""Plan event-spanning NOAA/CIRA GraphCast forecast initializations."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .sources import NoaaGraphCastAdapter, USER_AGENT, available_forecast_steps
from .update import read_manifest
from .versions import model_version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas-core", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--spacing-hours", type=int, default=48)
    inventory = parser.add_mutually_exclusive_group()
    inventory.add_argument("--inventory", type=Path, help="pinned NOAA S3 inventory JSON")
    inventory.add_argument(
        "--fetch-inventory",
        action="store_true",
        help="list the current public GraphCast objects before planning",
    )
    parser.add_argument("--save-inventory", type=Path)
    return parser.parse_args()


def floor_twelve_hour_cycle(value: datetime) -> datetime:
    value = value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return value - timedelta(hours=value.hour % 12)


def fetch_inventory() -> list[dict[str, object]]:
    """List GraphCast objects from NOAA's anonymous S3 endpoint."""

    rows: list[dict[str, object]] = []
    token = ""
    while True:
        query = {
            "list-type": "2",
            "prefix": "GRAP_v100_GFS/",
            "max-keys": "1000",
        }
        if token:
            query["continuation-token"] = token
        url = f"{NoaaGraphCastAdapter.ROOT}/?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            root = ET.fromstring(response.read())
        namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        for item in root.findall("s3:Contents", namespace):
            key = item.findtext("s3:Key", default="", namespaces=namespace)
            match = re.search(r"_(\d{10})_f000_f240_06\.nc$", key)
            if not match:
                continue
            rows.append({
                "cycle": match.group(1),
                "key": key,
                "last_modified": item.findtext(
                    "s3:LastModified", default="", namespaces=namespace
                ),
                "bytes": int(item.findtext("s3:Size", default="0", namespaces=namespace)),
            })
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=namespace)
        if truncated.lower() != "true":
            break
        token = root.findtext(
            "s3:NextContinuationToken", default="", namespaces=namespace
        )
        if not token:
            raise RuntimeError("NOAA S3 inventory is truncated without a continuation token")
    return sorted(rows, key=lambda item: str(item["cycle"]))


def inventory_rows(args: argparse.Namespace) -> list[dict[str, object]] | None:
    if args.inventory:
        payload = json.loads(args.inventory.read_text(encoding="utf-8"))
        return list(payload.get("objects", payload)) if isinstance(payload, dict) else list(payload)
    if not args.fetch_inventory:
        return None
    rows = fetch_inventory()
    if args.save_inventory:
        atomic_write_json(args.save_inventory, {
            "schema": "mla-noaa-graphcast-inventory-v1",
            "generated_utc": iso_z(utc_now()),
            "source": f"{NoaaGraphCastAdapter.ROOT}/GRAP_v100_GFS/",
            "objects": rows,
        })
    return rows


def main() -> None:
    args = parse_args()
    if args.spacing_hours < 12 or args.spacing_hours % 12:
        raise ValueError("spacing-hours must be a positive multiple of twelve")
    with gzip.open(args.atlas_core, "rt", encoding="utf-8") as stream:
        core = json.load(stream)
    fields = {name: index for index, name in enumerate(core["track_fields"])}
    coverage_end = datetime.fromisoformat(
        str(core["meta"]["coverage_end"]).replace("Z", "+00:00")
    )
    cycles: set[datetime] = set()
    eligible_tracks = 0
    for row in core["tracks"]:
        start = datetime.fromtimestamp(int(row[fields["start_ms"]]) / 1000, tz=UTC)
        end = datetime.fromtimestamp(int(row[fields["end_ms"]]) / 1000, tz=UTC)
        if end < NoaaGraphCastAdapter.START:
            continue
        eligible_tracks += 1
        cycle = floor_twelve_hour_cycle(
            max(NoaaGraphCastAdapter.START, start - timedelta(hours=24))
        )
        while cycle <= min(end, coverage_end):
            cycles.add(cycle)
            cycle += timedelta(hours=args.spacing_hours)

    model = "graphcast-noaa"
    inventory = inventory_rows(args)
    available_cycles = None if inventory is None else {
        str(item["cycle"]) for item in inventory
    }
    desired_unfiltered = [
        {
            "model": model,
            "cycle": cycle.strftime("%Y%m%d%H"),
            "cycle_utc": iso_z(cycle),
            "first_step_hours": 0,
            "horizon_hours": available_forecast_steps(model, cycle)[-1],
            "model_version": model_version(model, cycle),
        }
        for cycle in sorted(cycles)
    ]
    desired = [
        item for item in desired_unfiltered
        if available_cycles is None or str(item["cycle"]) in available_cycles
    ]
    available: dict[str, int] = {}
    if args.manifest and args.manifest.exists():
        manifest = read_manifest(args.manifest)
        available = {
            f"{item.get('model')}:{item.get('cycle')}": manifest_entry_horizon_hours(item)
            for item in manifest.get("archive", [])
        }
    pending = [
        item for item in desired
        if available.get(f"{model}:{item['cycle']}", -1) < int(item["horizon_hours"])
    ]
    plan = {
        "schema": "mla-forecast-graphcast-archive-plan-v1",
        "manifest_key": "graphcast_noaa_archive",
        "generated_utc": iso_z(utc_now()),
        "models": [model],
        "providers": ["NOAA/CIRA AIWP archive"],
        "provider_earliest_cycle_utc": iso_z(NoaaGraphCastAdapter.START),
        "availability_policy": (
            "current anonymous NOAA S3 object inventory"
            if inventory is not None else "nominal NOAA/CIRA archive coverage"
        ),
        "inventory_objects": len(inventory) if inventory is not None else None,
        "unavailable_selected_cycles": len(desired_unfiltered) - len(desired),
        "catalogue_version": str(core["meta"].get("catalogue_version", "v5.6")),
        "catalogue_coverage_end": iso_z(coverage_end),
        "selection_policy": (
            f"00/12 UTC initialization 24 h before each ERA5 event, then every "
            f"{args.spacing_hours} h through its published lifetime; duplicate cycles collapsed"
        ),
        "cycle_payload_policy": (
            "all 41 six-hourly valid times from +0 to +240 h, GFS-initialized GraphCast "
            "Operational output, and every track published by the frozen atlas detector/linker"
        ),
        "eligible_catalogue_tracks": eligible_tracks,
        "desired_cycles": len(desired),
        "already_available_cycles": len(desired) - len(pending),
        "cycles": desired,
        "pending_cycles": pending,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(
            f"{index}\t{model}\t{item['cycle']}\t{item['horizon_hours']}\t0\n"
            for index, item in enumerate(pending, 1)
        ),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(
        f"Planned {len(desired)} available GraphCast cycles; {len(pending)} remain to build "
        f"({len(desired_unfiltered) - len(desired)} selected cycles absent from the provider inventory)"
    )


if __name__ == "__main__":
    main()
