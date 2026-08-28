#!/usr/bin/env python3
"""Build a small-multiple LPS track-density figure for boundary worldviews.

Chart contract
--------------
Question: How does the cartographic outline of India change across selected
Natural Earth administrative-boundary point-of-view (POV) products while the
underlying LPS track-density evidence is held fixed?
Grain: one physical event contributes at most once to each 1-degree cell.
Comparison: eleven panels share the same extent, density field, normalisation
and palette; only the Natural Earth boundary worldview changes. The 34 source
products are collapsed into the 11 distinct India geometries they contain.
Boundary source: Natural Earth 1:10m admin-0 countries, version 5.1.1.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from build_track_diagnostic_figure import (
    ATLAS_BLUE,
    ATLAS_ROOT,
    DEFAULT_DATA,
    GRID,
    INDIGO,
    INK,
    LAND,
    MADDER,
    MUTED,
    PAPER,
    SEA,
    configure_style,
    load_data,
    unique_track_density,
)


DEFAULT_OUTPUT = ATLAS_ROOT / "figures/lps-v5.6-track-density-boundary-worldviews.png"
MAP_EXTENT = (66.0, 101.0, 5.0, 38.0)  # west, east, south, north
# Dissolved India geometry groups across all 34 Natural Earth v5.1.1 POVs:
# default; ind; pak/tur; chn/twn; nep; gbr; usa; rus; isr; iso/tlc; and
# arg/bdg/bra/deu/egy/esp/fra/grc/idn/ita/jpn/kor/mar/nld/pol/prt/pse/sau/swe/ukr/vnm.
VIEWPOINTS = (
    {
        "key": "default",
        "title": "Default (de facto)",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
        "sha256": "ce1ac7036499a0edd641fbc093cd209a98f96a49d2eca8480aaacad35138a7f6",
    },
    {
        "key": "ind",
        "title": "India POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_ind.zip",
        "sha256": "36a54ee97a509325e98eb44fd91bc7e53b0c5139cf10039918042bbd64102192",
    },
    {
        "key": "pak",
        "title": "Pakistan / Turkey POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_pak.zip",
        "sha256": "cb6730734f7ffd64a087ec8cd801291ddf4507611c9902bd800362f2813a653b",
    },
    {
        "key": "chn",
        "title": "China / Taiwan POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_chn.zip",
        "sha256": "16e7589083527d01208b9f645fc8643c767170258e9d13b59d37bc5a1f6a8758",
    },
    {
        "key": "nep",
        "title": "Nepal POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_nep.zip",
        "sha256": "4755295da115a74668886f37db1b8a6b631a28e5c30a5cf7e47d799565a6d365",
    },
    {
        "key": "gbr",
        "title": "United Kingdom POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_gbr.zip",
        "sha256": "23e586f7225a4a83b20178b78b8b8209b14bc6c385b09add44e3a2e50ac6dac3",
    },
    {
        "key": "usa",
        "title": "United States POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_usa.zip",
        "sha256": "4dd67d07246421372a3ea069d0b58b8ae28fe42c6a787a18411025b68f614d03",
    },
    {
        "key": "rus",
        "title": "Russia POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_rus.zip",
        "sha256": "8309218a2d9b0b2a7f218a03fa05825a7757390ccec4d793e6e8e6a6015dd04d",
    },
    {
        "key": "isr",
        "title": "Israel POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_isr.zip",
        "sha256": "36c2af239f9a04d2d104e893ce82ca52fea7dafbceac91a09be5b9c0569ecf68",
    },
    {
        "key": "iso",
        "title": "ISO / top-level POV",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_iso.zip",
        "sha256": "9a097f5c5fe0cd2e0f1f120a507f9f8e421c6ff7e20a2e4c8621311081b55453",
    },
    {
        "key": "bdg",
        "title": "Bangladesh + 20 POVs",
        "url": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries_bdg.zip",
        "sha256": "38dc28eea054c58cd828ca846cd08bd31007451b5c3591df0d73483d24380ed7",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--boundary-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "lps-natural-earth-pov-v5.1.1",
        help="Download/cache directory for Natural Earth POV ZIP files.",
    )
    parser.add_argument("--font", type=Path, help="Optional local Effra font file.")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_boundary_archive(spec: dict[str, str], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"ne_10m_admin_0_countries_{spec['key']}.zip"
    if destination.is_file():
        actual = sha256(destination)
        if actual != spec["sha256"]:
            raise ValueError(f"Cached Natural Earth archive has unexpected SHA-256: {destination}")
        return destination
    temporary = destination.with_suffix(".zip.part")
    try:
        with urllib.request.urlopen(spec["url"], timeout=120) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        actual = sha256(temporary)
        if actual != spec["sha256"]:
            raise ValueError(f"Downloaded Natural Earth archive has unexpected SHA-256: {spec['key']}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_worldview(spec: dict[str, str], directory: Path) -> gpd.GeoDataFrame:
    archive = fetch_boundary_archive(spec, directory)
    west, east, south, north = MAP_EXTENT
    world = gpd.read_file(archive, engine="pyogrio", bbox=(west, south, east, north))
    if world.crs is None or not world.crs.is_geographic:
        world = world.to_crs("EPSG:4326")
    india = world[world["ADMIN"].eq("India")]
    if india.empty or india.geometry.is_empty.any():
        raise ValueError(f"Natural Earth {spec['key']} POV does not contain valid India geometry")
    return world


def build_figure(frame, worldviews: list[tuple[dict[str, str], gpd.GeoDataFrame]]) -> plt.Figure:
    lon_edges = np.arange(52, 110, 1)
    lat_edges = np.arange(-4, 39, 1)
    density = unique_track_density(frame, lon_edges, lat_edges)
    density = np.ma.masked_where(density == 0, density)
    colour_map = mpl.colors.LinearSegmentedColormap.from_list(
        "atlas_density", ["#dbe8f2", "#9bbdd3", ATLAS_BLUE, INDIGO, "#35204e"]
    )
    normalisation = mpl.colors.PowerNorm(gamma=0.5, vmin=1, vmax=float(density.max()))

    figure = plt.figure(figsize=(18.5, 13.5))
    grid = figure.add_gridspec(3, 8)
    panel_spans = [
        (0, 0, 2), (0, 2, 4), (0, 4, 6), (0, 6, 8),
        (1, 0, 2), (1, 2, 4), (1, 4, 6), (1, 6, 8),
        (2, 1, 3), (2, 3, 5), (2, 5, 7),
    ]
    axes: list[plt.Axes] = []
    for row, start, stop in panel_spans:
        shared = axes[0] if axes else None
        axes.append(figure.add_subplot(grid[row, start:stop], sharex=shared, sharey=shared))
    figure.subplots_adjust(left=0.055, right=0.945, bottom=0.07, top=0.965, wspace=0.08, hspace=0.17)

    mesh = None
    west, east, south, north = MAP_EXTENT
    for panel_index, (ax, (spec, world)) in enumerate(zip(axes, worldviews)):
        ax.set_facecolor(SEA)
        world.plot(ax=ax, color=LAND, edgecolor="none", zorder=1)
        mesh = ax.pcolormesh(
            lon_edges,
            lat_edges,
            density,
            cmap=colour_map,
            norm=normalisation,
            shading="flat",
            alpha=0.9,
            zorder=2,
        )
        world.boundary.plot(ax=ax, color="#746855", linewidth=0.48, alpha=0.82, zorder=4)
        world[world["ADMIN"].eq("India")].boundary.plot(ax=ax, color=MADDER, linewidth=1.35, zorder=5)
        ax.set_xlim(west, east)
        ax.set_ylim(south, north)
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(f"({chr(97 + panel_index)}) {spec['title']}", loc="left")
        ax.set_xticks([70, 80, 90, 100])
        ax.set_yticks([10, 20, 30])
        ax.grid(True, color=GRID, linewidth=0.65, alpha=0.62)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(length=3, width=0.7, colors=MUTED)

    for ax in axes[8:]:
        ax.set_xlabel("Longitude (°E)")
    for ax in (axes[0], axes[4], axes[8]):
        ax.set_ylabel("Latitude (°N)")

    if mesh is None:
        raise RuntimeError("No worldview panels were drawn")
    colour_bar = figure.colorbar(mesh, ax=axes, location="right", fraction=0.025, pad=0.024)
    colour_bar.set_label("Tracks per 1° cell")
    colour_bar.outline.set_edgecolor(GRID)
    figure.legend(
        handles=(
            Line2D([0], [0], color=MADDER, linewidth=1.6, label="India outline in selected POV"),
            Line2D([0], [0], color="#746855", linewidth=0.7, label="Other country outlines"),
        ),
        loc="lower center",
        bbox_to_anchor=(0.49, 0.015),
        ncol=2,
        handlelength=2.6,
        columnspacing=1.8,
        frameon=False,
    )
    return figure


def main() -> None:
    args = parse_args()
    configure_style(args.font)
    frame = load_data(args.data)
    if frame.empty or frame["track_id"].nunique() < 1:
        raise ValueError("Expected a non-empty physical-event catalogue")
    worldviews = [(spec, load_worldview(spec, args.boundary_dir)) for spec in VIEWPOINTS]
    india_geometries = [world.loc[world["ADMIN"].eq("India"), "geometry"].union_all() for unused, world in worldviews]
    if len({geometry.normalize().wkb for geometry in india_geometries}) != len(india_geometries):
        raise ValueError("Selected Natural Earth POVs do not provide 11 distinct India geometries")
    figure = build_figure(frame, worldviews)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        args.output,
        dpi=190,
        metadata={
            "Software": "matplotlib/geopandas",
            "Title": "LPS v5.6 track density with alternative boundary worldviews for India",
            "Description": "Eleven distinct India outlines across 34 Natural Earth v5.1.1 admin-0 POV products; identical geometries are grouped and boundary variants are dataset worldviews, not endorsements.",
        },
    )
    args.output.chmod(0o644)
    plt.close(figure)
    print(f"Wrote {args.output} from {frame['track_id'].nunique():,} physical events")


if __name__ == "__main__":
    main()
