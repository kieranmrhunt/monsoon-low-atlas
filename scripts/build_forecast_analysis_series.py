#!/usr/bin/env python3
"""Build the compact ERA5 diagnostics used by Forecast Archive timelines."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


HOUR_MS = 3_600_000


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    core = read_gzip_json(args.core)
    detail = read_gzip_json(args.detail)
    track_fields = {name: index for index, name in enumerate(core["track_fields"])}
    series_fields = {name: index for index, name in enumerate(core["series_fields"])}
    records: dict[str, list[list[int | None]]] = {}
    for row, series in zip(core["tracks"], detail["series"], strict=True):
        start_hour = int(round(int(row[track_fields["start_ms"]]) / HOUR_MS))
        hours = series[series_fields["hours_since_genesis"]]
        vorticity = series[series_fields["vort_smooth_x10"]]
        precipitation = series[series_fields["precip24_x10"]]
        records[str(row[track_fields["id"]])] = [
            [
                start_hour + int(hour),
                None if vort is None else int(vort),
                None if rain is None else int(rain),
            ]
            for hour, vort, rain in zip(hours, vorticity, precipitation, strict=True)
        ]

    payload = {
        "schema": "mla-forecast-era5-analysis-series-v1",
        "catalogue": core.get("meta", {}).get("catalogue", "ERA5 v5.6"),
        "point_fields": ["epoch_hour", "vort_smooth_x10", "precip24_x10"],
        "tracks": records,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    output = args.output_dir / f"atlas-analysis-series.{digest}.json.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw_stream:
        with gzip.GzipFile(fileobj=raw_stream, mode="wb", compresslevel=9, mtime=0) as stream:
            stream.write(raw)
    print(output)


if __name__ == "__main__":
    main()
