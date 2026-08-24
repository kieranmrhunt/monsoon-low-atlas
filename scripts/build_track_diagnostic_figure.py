#!/usr/bin/env python3
"""Build the atlas four-panel track-catalogue diagnostic figure.

Chart contract
--------------
Question: Which spatial, seasonal, track-geometry and lifecycle structures are
most useful for a compact introduction to the public v5.4.2 catalogue?
Grain: one physical event for panels a-c; one event per normalised-life bin
before cross-event aggregation for panel d.
Metrics: genesis count per 1-degree cell, genesis month by peak atlas class,
hourly-centre path length against duration, and pressure-deficit median/IQR.
Missing values: finite values only; each event contributes at most once to any
aggregate cell or life bin.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.patches import Polygon


ATLAS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ATLAS_ROOT.parent
DEFAULT_DATA = (
    WORKSPACE_ROOT
    / "lps-v5.3-continuity-framework/production/v5.4.2/zenodo-release"
    / "lps_v5.4.2-era5-1940-2025-core.parquet"
)
DEFAULT_CORE = ATLAS_ROOT / "assets/atlas-core.c6db29ed4192.json.gz"
DEFAULT_OUTPUT = ATLAS_ROOT / "figures/lps-v5.4.2-track-diagnostics.png"

PAPER = "#fffaf0"
INK = "#282119"
MUTED = "#685c4d"
GRID = "#d9d0bd"
LAND = "#f3e6c8"
SEA = "#e7eee7"
INDIGO = "#233f78"
PEACOCK = "#08736f"
CLASS_COLOURS = ["#c3931d", "#c9631b", "#ad4328", "#8f2938", "#64224f", "#35204e"]
CLASS_LABELS = ["L", "D", "DD", "CS", "SCS", "VSCS+"]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--font",
        type=Path,
        help="Optional local Effra font file. Arial is used when it is unavailable.",
    )
    return parser.parse_args()


def configure_style(font_path: Path | None) -> None:
    family = "Arial"
    if font_path and font_path.is_file():
        fontManager.addfont(font_path)
        family = FontProperties(fname=font_path).get_name()
    mpl.rcParams.update(
        {
            "font.family": family,
            "font.size": 10.5,
            "axes.titlesize": 13.5,
            "axes.titleweight": 500,
            "axes.labelsize": 10.5,
            "axes.labelcolor": INK,
            "axes.edgecolor": GRID,
            "axes.linewidth": 0.8,
            "axes.facecolor": PAPER,
            "axes.axisbelow": True,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "text.color": INK,
            "legend.frameon": False,
            "figure.facecolor": PAPER,
            "savefig.facecolor": PAPER,
            "savefig.bbox": "tight",
        }
    )


def load_data(path: Path) -> pd.DataFrame:
    columns = [
        "track_id",
        "time",
        "lon",
        "lat",
        "track_duration_hours",
        "imd_category",
        "pressure_deficit_hpa",
    ]
    frame = pd.read_parquet(path, columns=columns)
    return frame.sort_values(["track_id", "time"], kind="stable").reset_index(drop=True)


def event_summary(frame: pd.DataFrame) -> pd.DataFrame:
    group = frame.groupby("track_id", sort=False, observed=True)
    summary = pd.DataFrame(
        {
            "genesis_time": group["time"].first(),
            "genesis_lon": group["lon"].first(),
            "genesis_lat": group["lat"].first(),
            "duration_hours": group["track_duration_hours"].first(),
            "peak_class": group["imd_category"].max(),
        }
    )

    previous_lat = group["lat"].shift()
    previous_lon = group["lon"].shift()
    lat1 = np.deg2rad(previous_lat.to_numpy(dtype=float))
    lat2 = np.deg2rad(frame["lat"].to_numpy(dtype=float))
    delta_lat = lat2 - lat1
    delta_lon = np.deg2rad(frame["lon"].to_numpy(dtype=float) - previous_lon.to_numpy(dtype=float))
    haversine = np.sin(delta_lat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(delta_lon / 2) ** 2
    frame = frame.assign(step_km=6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(haversine, 0, 1))))
    summary["path_km"] = frame.groupby("track_id", sort=False, observed=True)["step_km"].sum(min_count=1)
    summary["genesis_month"] = pd.to_datetime(summary["genesis_time"], utc=True).dt.month
    return summary


def lifecycle_summary(frame: pd.DataFrame) -> pd.DataFrame:
    group = frame.groupby("track_id", sort=False, observed=True)
    order = group.cumcount().to_numpy(dtype=float)
    length = group["track_id"].transform("size").to_numpy(dtype=float)
    life_fraction = np.divide(order, np.maximum(1, length - 1), out=np.zeros_like(order), where=length > 1)
    life_bin = np.clip(np.rint(life_fraction * 25), 0, 25).astype(int) * 4
    values = frame[["track_id", "pressure_deficit_hpa"]].assign(life_fraction=life_bin)
    values = values[np.isfinite(values["pressure_deficit_hpa"])]
    per_event = values.groupby(["track_id", "life_fraction"], observed=True)["pressure_deficit_hpa"].median()
    by_bin = per_event.groupby("life_fraction")
    return pd.DataFrame(
        {
            "median": by_bin.median(),
            "q1": by_bin.quantile(0.25),
            "q3": by_bin.quantile(0.75),
        }
    ).reset_index()


def load_geography(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)["geo"]


def draw_geography(ax: plt.Axes, geography: dict, outlines_only: bool = False) -> None:
    if not outlines_only:
        for ring in geography["land"]:
            ax.add_patch(Polygon(ring, closed=True, facecolor=LAND, edgecolor="none", zorder=1))
    for ring in geography["land"]:
        points = np.asarray(ring)
        ax.plot(points[:, 0], points[:, 1], color="#746855", linewidth=0.45, alpha=0.72, zorder=4)
    for border in geography.get("borders", []):
        points = np.asarray(border.get("p", []))
        if len(points) < 2:
            continue
        ax.plot(
            points[:, 0],
            points[:, 1],
            color="#746855",
            linewidth=0.38,
            linestyle=(0, (3, 2)) if border.get("c") == 1 else "solid",
            alpha=0.66,
            zorder=4,
        )
    for state in geography.get("states", []):
        for ring in state.get("rings", []):
            points = np.asarray(ring)
            ax.plot(points[:, 0], points[:, 1], color=INDIGO, linewidth=0.28, alpha=0.46, zorder=4)


def style_axis(ax: plt.Axes, grid_axis: str = "both") -> None:
    ax.grid(True, axis=grid_axis, color=GRID, linewidth=0.65, alpha=0.62)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(length=3, width=0.7)


def build_figure(frame: pd.DataFrame, summary: pd.DataFrame, geography: dict) -> plt.Figure:
    lifecycle = lifecycle_summary(frame)
    figure, axes = plt.subplots(2, 2, figsize=(14.2, 9.4), constrained_layout=True)
    figure.get_layout_engine().set(w_pad=0.07, h_pad=0.08, wspace=0.04, hspace=0.06)

    # (a) One genesis point per event, aggregated on a transparent 1-degree grid.
    ax = axes[0, 0]
    ax.set_facecolor(SEA)
    draw_geography(ax, geography)
    lon_edges = np.arange(52, 109, 1)
    lat_edges = np.arange(-4, 37, 1)
    density, _, _ = np.histogram2d(summary["genesis_lon"], summary["genesis_lat"], bins=[lon_edges, lat_edges])
    density = np.ma.masked_where(density.T == 0, density.T)
    colour_map = mpl.colors.LinearSegmentedColormap.from_list(
        "atlas_density", ["#efe8b0", "#dfb42a", "#c9631b", "#8f2938", "#35204e"]
    )
    mesh = ax.pcolormesh(
        lon_edges,
        lat_edges,
        density,
        cmap=colour_map,
        norm=mpl.colors.PowerNorm(gamma=0.5, vmin=1, vmax=float(density.max())),
        shading="flat",
        alpha=0.92,
        zorder=3,
    )
    draw_geography(ax, geography, outlines_only=True)
    ax.set(xlim=(52, 108), ylim=(-4, 36), xlabel="Longitude (°E)", ylabel="Latitude (°N)")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("(a) Genesis density", loc="left")
    style_axis(ax)
    colour_bar = figure.colorbar(mesh, ax=ax, location="right", fraction=0.036, pad=0.025)
    colour_bar.set_label("Systems per 1° cell")
    colour_bar.outline.set_edgecolor(GRID)

    # (b) Genesis season, separated by the maximum atlas-derived class reached.
    ax = axes[0, 1]
    bottom = np.zeros(12)
    for category, (label, colour) in enumerate(zip(CLASS_LABELS, CLASS_COLOURS), start=1):
        counts = np.array(
            [((summary["genesis_month"] == month) & (summary["peak_class"] == category)).sum() for month in range(1, 13)]
        )
        ax.bar(np.arange(12), counts, width=0.74, bottom=bottom, color=colour, label=label, linewidth=0)
        bottom += counts
    ax.set_xticks(np.arange(12), MONTH_LABELS, rotation=35, ha="right")
    ax.set(xlabel="Genesis month", ylabel="Systems")
    ax.set_title("(b) Seasonality and peak class", loc="left")
    ax.legend(ncol=3, loc="upper left", handlelength=1.1, columnspacing=1.1)
    style_axis(ax, "y")

    # (c) Geometry at event grain; all hourly steps contribute to path length.
    ax = axes[1, 0]
    for category, (label, colour) in enumerate(zip(CLASS_LABELS, CLASS_COLOURS), start=1):
        subset = summary[summary["peak_class"] == category]
        ax.scatter(
            subset["duration_hours"] / 24,
            subset["path_km"],
            s=12 if category < 5 else 24,
            color=colour,
            alpha=0.55 if category < 5 else 0.82,
            linewidths=0,
            label=label,
        )
    ax.set(xlabel="Duration (days)", ylabel="Path length (km)")
    ax.set_title("(c) Track duration and path length", loc="left")
    ax.legend(ncol=3, loc="upper left", handletextpad=0.35, columnspacing=1.0)
    style_axis(ax)

    # (d) Each event contributes one within-event median to each life-fraction bin.
    ax = axes[1, 1]
    x = lifecycle["life_fraction"].to_numpy(dtype=float)
    q1 = lifecycle["q1"].to_numpy(dtype=float)
    q3 = lifecycle["q3"].to_numpy(dtype=float)
    median = lifecycle["median"].to_numpy(dtype=float)
    ax.fill_between(x, q1, q3, color=PEACOCK, alpha=0.20, linewidth=0, label="IQR")
    ax.plot(x, median, color=PEACOCK, linewidth=2.5, label="Median")
    ax.set(xlim=(0, 100), xlabel="Fraction of event lifetime (%)", ylabel="Pressure deficit (hPa)")
    ax.set_title("(d) Pressure-deficit lifecycle", loc="left")
    ax.legend(loc="upper left", ncol=2)
    style_axis(ax)

    return figure


def main() -> None:
    args = parse_args()
    configure_style(args.font)
    frame = load_data(args.data)
    summary = event_summary(frame)
    if len(summary) != 2_980 or not summary.index.is_unique:
        raise ValueError(f"Expected 2,980 unique physical events, found {len(summary):,}")
    required = summary[["genesis_lon", "genesis_lat", "duration_hours", "path_km", "peak_class"]]
    if not np.isfinite(required.to_numpy(dtype=float)).all():
        raise ValueError("Non-finite event summary values would make the figure incomplete")
    if (summary["path_km"] < 0).any() or not summary["peak_class"].between(1, 6).all():
        raise ValueError("Event path lengths or peak classes are outside their valid domains")
    geography = load_geography(args.core)
    figure = build_figure(frame, summary, geography)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=190, metadata={"Software": "matplotlib", "Title": "LPS v5.4.2 track diagnostics"})
    plt.close(figure)
    print(f"Wrote {args.output} from {len(summary):,} physical events and {len(frame):,} hourly positions")


if __name__ == "__main__":
    main()
