#!/usr/bin/env python3
"""Build the India-view boundary asset from the official Survey of India outline."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

import geopandas as gpd
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon


SOURCE_PAGE = "https://surveyofindia.gov.in/pages/outline-maps-of-india"
SOURCE_URL = "https://surveyofindia.gov.in/documents/Outline_of_India.zip"
SOURCE_SHA256 = "bf48477f01fe8addd6384490fc6f8decc9643110331ffef2c3f17e5cccd53b88"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--simplify-degrees", type=float, default=0.02)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_archive(path: Path | None) -> Path:
    destination = path or Path(tempfile.gettempdir()) / "Outline_of_India.zip"
    if not destination.exists():
        urllib.request.urlretrieve(SOURCE_URL, destination)
    if sha256(destination) != SOURCE_SHA256:
        raise ValueError("Survey of India outline SHA-256 changed; review the new source before rebuilding")
    return destination


def polygon_rings(geometry, digits: int = 4) -> list[list[list[float]]]:
    polygons = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    rings = []
    for polygon in polygons:
        for ring in [polygon.exterior, *polygon.interiors]:
            coordinates = [[round(float(x), digits), round(float(y), digits)] for x, y in ring.coords]
            if len(coordinates) >= 4:
                rings.append(coordinates)
    return rings


def line_parts(geometry):
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [part for item in geometry.geoms for part in line_parts(item)]
    return []


def main() -> None:
    args = parse_args()
    archive = source_archive(args.archive)
    official = gpd.read_file(f"zip://{archive.resolve()}").to_crs(4326).geometry.union_all()
    official = official.simplify(args.simplify_degrees, preserve_topology=True)
    if official.is_empty or not official.is_valid:
        raise ValueError("Survey of India outline is empty or invalid")

    with gzip.open(args.core, "rt", encoding="utf-8") as handle:
        core = json.load(handle)
    exclusion = official.buffer(0.04)
    borders = []
    for border in core["geo"]["borders"]:
        points = border.get("p", [])
        if len(points) < 2:
            continue
        remaining = LineString(points).difference(exclusion)
        for part in line_parts(remaining):
            coordinates = [[round(float(x), 3), round(float(y), 3)] for x, y in part.coords]
            if len(coordinates) >= 2:
                borders.append({"c": int(border.get("c", 0)), "p": coordinates})

    payload = {
        "schema": "monsoon-low-atlas-soi-boundary-v1",
        "source": "Survey of India official International Boundary Vector data (Outline of India)",
        "source_page": SOURCE_PAGE,
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "scale": "1:16 million",
        "simplification_degrees": args.simplify_degrees,
        "use": "Individual, internal, educational, research and website purposes; non-commercial.",
        "copyright": "Government of India / Survey of India",
        "rings": polygon_rings(official),
        "borders_elsewhere": borders,
    }
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()[:12]
    filename = f"atlas-soi-boundary.{digest}.json.gz"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / filename).write_bytes(compressed)
    print(json.dumps({
        "asset": filename,
        "rings": len(payload["rings"]),
        "borders_elsewhere": len(borders),
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
    }, indent=2))


if __name__ == "__main__":
    main()
