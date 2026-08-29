#!/usr/bin/env python3
"""Pack per-system vertical composites for responsive filtered-subset means."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import sys
from array import array
from datetime import datetime, timezone
from pathlib import Path


NULL_I16 = -32768
FIELDS = ("relative_vorticity", "theta_e")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_gzip_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--composite-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--longitude-step", default=0.5, type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    core = load_gzip_json(args.core)
    track_fields = {name: index for index, name in enumerate(core["track_fields"])}
    track_ids = [int(row[track_fields["id"]]) for row in core["tracks"]]
    manifest_path = args.composite_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("complete") or int(manifest.get("completed_tracks", 0)) != len(track_ids):
        raise RuntimeError("Composite archive is not complete for the atlas catalogue")

    packed = {field: array("h") for field in FIELDS}
    field_meta: dict[str, dict] = {}
    field_sources = {field: set() for field in FIELDS}
    pressure_hpa = None
    longitude = None
    source_longitude = None
    longitude_columns = None
    sample_counts = {field: [] for field in FIELDS}

    for position, track_id in enumerate(track_ids, start=1):
        path = args.composite_dir / "tracks" / f"track-{track_id}.json.gz"
        asset = load_gzip_json(path)
        if int(asset.get("track_id", -1)) != track_id:
            raise RuntimeError(f"Composite identity mismatch for event {track_id}")
        if pressure_hpa is None:
            pressure_hpa = [float(value) for value in asset["grid"]["pressure_hpa"]]
            source_longitude = dict(asset["grid"]["relative_longitude_degrees"])
            stride = round(args.longitude_step / float(source_longitude["step"]))
            if stride < 1 or abs(stride * float(source_longitude["step"]) - args.longitude_step) > 1e-8:
                raise RuntimeError("Requested longitude step is not an integer multiple of the source grid")
            longitude_columns = list(range(0, int(source_longitude["count"]), stride))
            longitude = {
                "start": float(source_longitude["start"]),
                "step": float(source_longitude["step"]) * stride,
                "count": len(longitude_columns),
            }
        elif pressure_hpa != [float(value) for value in asset["grid"]["pressure_hpa"]]:
            raise RuntimeError(f"Pressure grid mismatch for event {track_id}")

        for field_name in FIELDS:
            field = asset.get("section", {}).get(field_name)
            if field is None:
                raise RuntimeError(f"Missing {field_name} section for event {track_id}")
            shape = [int(value) for value in field["shape"]]
            values = field["data"]
            if shape != [len(pressure_hpa), int(source_longitude["count"])] or len(values) != shape[0] * shape[1]:
                raise RuntimeError(f"Unexpected {field_name} shape for event {track_id}: {shape}")
            packed_values = [
                values[row * shape[1] + column]
                for row in range(shape[0])
                for column in longitude_columns
            ]
            meta = {
                "shape_per_track": [shape[0], len(longitude_columns)],
                "scale": float(field["scale"]),
                "units": field["units"],
            }
            if field_name in field_meta and field_meta[field_name] != meta:
                raise RuntimeError(f"Inconsistent {field_name} metadata for event {track_id}")
            field_meta[field_name] = meta
            field_sources[field_name].add(str(field["source"]))
            sample_counts[field_name].append(int(field.get("samples", 0)))
            packed[field_name].extend(NULL_I16 if value is None else int(value) for value in packed_values)

        if position % 250 == 0 or position == len(track_ids):
            print(f"Packed {position}/{len(track_ids)} events", file=sys.stderr)

    fields = {}
    for field_name in FIELDS:
        values = packed[field_name]
        if sys.byteorder != "little":
            values.byteswap()
        meta = field_meta[field_name]
        fields[field_name] = {
            **meta,
            "sources": sorted(field_sources[field_name]),
            "encoding": "base64 little-endian signed int16",
            "null_value": NULL_I16,
            "data_b64": base64.b64encode(values.tobytes()).decode("ascii"),
            "sample_count_min": min(sample_counts[field_name]),
            "sample_count_max": max(sample_counts[field_name]),
        }

    built_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema": "monsoon-low-atlas-subset-sections-v1",
        "release": core["meta"]["catalogue_version"],
        "track_count": len(track_ids),
        "track_ids": track_ids,
        "grid": {
            "relative_longitude_degrees": longitude,
            "pressure_hpa": pressure_hpa,
            "frame": "unrotated storm-relative zonal section at zero relative latitude",
        },
        "fields": fields,
        "method": manifest["method"]["vertical"],
        "source": {
            "composite_manifest": f"{args.composite_dir.name}/manifest.json",
            "composite_manifest_sha256": sha256(manifest_path),
            "core_asset": args.core.name,
            "core_asset_sha256": sha256(args.core),
        },
        "built_utc": built_utc,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f"atlas-sections.{digest[:12]}.json.gz"
    destination.write_bytes(compressed)
    build_manifest_path = args.output_dir / "atlas-build-manifest.json"
    if build_manifest_path.exists():
        build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
        build_manifest.update({
            "sections": destination.name,
            "sections_built_utc": built_utc,
            "sections_sha256": digest,
            "sections_uncompressed_bytes": len(raw),
            "sections_compressed_bytes": len(compressed),
            "sections_qa": {
                "tracks": len(track_ids),
                "relative_longitude_step_degrees": longitude["step"],
                "relative_longitude_columns": longitude["count"],
                "pressure_levels": len(pressure_hpa),
                "lifecycle_snapshots_per_track": min(sample_counts[FIELDS[0]]),
                "fields": list(FIELDS),
            },
        })
        build_manifest_path.write_text(json.dumps(build_manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "path": str(destination),
        "sha256": digest,
        "raw_bytes": len(raw),
        "gzip_bytes": len(compressed),
        "tracks": len(track_ids),
    }, indent=2))


if __name__ == "__main__":
    main()
