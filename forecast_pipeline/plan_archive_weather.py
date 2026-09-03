#!/usr/bin/env python3
"""Plan weather enrichment for practical non-TIGGE forecast archives."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .forecast_core import atomic_write_json, iso_z, manifest_entry_horizon_hours, utc_now
from .update import read_manifest


REQUIRED_WEATHER_FIELDS = ("precipitation", "vorticity")
DEFAULT_MODELS = (
    "gfs",
    "gefs",
    "gefs-control",
    "ifs",
    "ukmo-global",
    "graphcast-noaa",
    "graphcast-ifs-noaa",
    "mogreps-g",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    return parser.parse_args()


def weather_backfill_cycles(
    manifest: dict,
    models: set[str],
) -> list[dict[str, object]]:
    """Return published archive cycles lacking either requested weather field."""

    required = set(REQUIRED_WEATHER_FIELDS)
    output = []
    for entry in manifest.get("archive", []):
        model = str(entry.get("model", ""))
        cycle = str(entry.get("cycle", ""))
        if model not in models or not cycle or required.issubset(
            set(entry.get("weather_fields", []))
        ):
            continue
        cycle_time = datetime.fromisoformat(
            str(entry["cycle_utc"]).replace("Z", "+00:00")
        )
        valid_start = datetime.fromisoformat(
            str(entry.get("valid_start_utc", entry["cycle_utc"])).replace("Z", "+00:00")
        )
        output.append({
            "model": model,
            "cycle": cycle,
            "cycle_utc": iso_z(cycle_time),
            "horizon_hours": manifest_entry_horizon_hours(entry),
            "first_step_hours": int(
                round((valid_start - cycle_time).total_seconds() / 3600)
            ),
            "model_version": entry.get("model_version", {}),
        })
    return sorted(
        output,
        key=lambda item: (str(item["cycle"]), str(item["model"])),
        reverse=True,
    )


def main() -> None:
    args = parse_args()
    models = {value.strip() for value in args.models.split(",") if value.strip()}
    unknown = models - set(DEFAULT_MODELS)
    if unknown:
        raise ValueError(f"unsupported archive-weather model(s): {', '.join(sorted(unknown))}")
    manifest = read_manifest(args.manifest)
    cycles = weather_backfill_cycles(manifest, models)
    plan = {
        "schema": "mla-forecast-archive-weather-plan-v1",
        "manifest_key": "archive_weather_backfill",
        "generated_utc": iso_z(utc_now()),
        "models": sorted(models),
        "providers": [
            "NOAA Open Data",
            "WeatherBench 2 / ECMWF Open Data",
            "CEDA/BADC Met Office Global archive",
            "NOAA/CIRA AIWP archive",
            "Met Office AWS Open Data",
        ],
        "required_weather_fields": list(REQUIRED_WEATHER_FIELDS),
        "selection_policy": "enrich existing non-TIGGE operational archive cycles without changing their selection",
        "cycle_payload_policy": "retain the published full valid-time axis and add 1-degree positive 850-hPa relative vorticity and trailing-24-hour precipitation",
        "cycles": cycles,
        "pending_cycles": cycles,
    }
    atomic_write_json(args.output, plan)
    args.jobs.parent.mkdir(parents=True, exist_ok=True)
    args.jobs.write_text(
        "".join(
            f"{index}\t{item['model']}\t{item['cycle']}\t{item['horizon_hours']}\t{item['first_step_hours']}\n"
            for index, item in enumerate(cycles, 1)
        ),
        encoding="utf-8",
    )
    args.jobs.chmod(0o644)
    print(f"Planned weather enrichment for {len(cycles)} non-TIGGE archive cycles")


if __name__ == "__main__":
    main()
