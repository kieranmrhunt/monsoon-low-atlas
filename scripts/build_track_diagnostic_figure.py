#!/usr/bin/env python3
"""Build the atlas four-panel track-catalogue diagnostic figure.

Chart contract
--------------
Question: Which spatial, seasonal, intensity and lifecycle structures are
most useful for a compact introduction to the public v5.4.2 catalogue?
Grain: one physical event for panels a-c; one event per normalised-life bin
before cross-event aggregation for panel d.
Metrics: unique-track count per 1-degree cell, genesis month by peak atlas class,
track-peak wind against pressure deficit, and four-metric lifecycle medians.
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
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon
from mpl_toolkits.axisartist.parasite_axes import HostAxes


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
LAND = "#e5e2dc"
SEA = "#f4f7f7"
INDIGO = "#233f78"
PEACOCK = "#08736f"
MADDER = "#aa3d2d"
ATLAS_BLUE = "#3978a8"
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
        "imd_category",
        "pressure_deficit_hpa",
        "max_wind",
        "precip_24hr",
        "rh850_mean_pct",
        "max_vort_smoothed",
    ]
    frame = pd.read_parquet(path, columns=columns)
    return frame.sort_values(["track_id", "time"], kind="stable").reset_index(drop=True)


def event_summary(frame: pd.DataFrame) -> pd.DataFrame:
    group = frame.groupby("track_id", sort=False, observed=True)
    summary = pd.DataFrame(
        {
            "genesis_time": group["time"].first(),
            "peak_class": group["imd_category"].max(),
            "peak_wind": group["max_wind"].max(),
            "peak_deficit": group["pressure_deficit_hpa"].max(),
        }
    )
    summary["genesis_month"] = pd.to_datetime(summary["genesis_time"], utc=True).dt.month
    return summary


def unique_track_density(frame: pd.DataFrame, lon_edges: np.ndarray, lat_edges: np.ndarray) -> np.ndarray:
    lon_bin = np.searchsorted(lon_edges, frame["lon"].to_numpy(dtype=float), side="right") - 1
    lat_bin = np.searchsorted(lat_edges, frame["lat"].to_numpy(dtype=float), side="right") - 1
    valid = (lon_bin >= 0) & (lon_bin < len(lon_edges) - 1) & (lat_bin >= 0) & (lat_bin < len(lat_edges) - 1)
    occupied = pd.DataFrame(
        {
            "track_id": frame.loc[valid, "track_id"].to_numpy(),
            "lon_bin": lon_bin[valid],
            "lat_bin": lat_bin[valid],
        }
    ).drop_duplicates(["track_id", "lon_bin", "lat_bin"])
    counts = occupied.groupby(["lat_bin", "lon_bin"], observed=True).size()
    density = np.zeros((len(lat_edges) - 1, len(lon_edges) - 1), dtype=int)
    for (lat_index, lon_index), count in counts.items():
        density[int(lat_index), int(lon_index)] = int(count)
    return density


def lifecycle_summaries(frame: pd.DataFrame, metrics: list[str]) -> dict[str, pd.DataFrame]:
    group = frame.groupby("track_id", sort=False, observed=True)
    order = group.cumcount().to_numpy(dtype=float)
    length = group["track_id"].transform("size").to_numpy(dtype=float)
    life_fraction = np.divide(order, np.maximum(1, length - 1), out=np.zeros_like(order), where=length > 1)
    life_bin = np.clip(np.rint(life_fraction * 25), 0, 25).astype(int) * 4
    summaries = {}
    for metric in metrics:
        values = frame[["track_id", metric]].assign(life_fraction=life_bin)
        values = values[np.isfinite(values[metric])]
        per_event = values.groupby(["track_id", "life_fraction"], observed=True)[metric].median()
        by_bin = per_event.groupby("life_fraction")
        summaries[metric] = pd.DataFrame(
            {
                "median": by_bin.median(),
            }
        ).reset_index()
    return summaries


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
    lifecycle = lifecycle_summaries(
        frame,
        ["pressure_deficit_hpa", "precip_24hr", "rh850_mean_pct", "max_vort_smoothed"],
    )
    figure = plt.figure(figsize=(14.2, 10.1), constrained_layout=True)
    grid = figure.add_gridspec(2, 2)
    lifecycle_grid = grid[1, 1].subgridspec(1, 2, width_ratios=[1, 0.16], wspace=0.02)
    axes = np.array(
        [
            [figure.add_subplot(grid[0, 0]), figure.add_subplot(grid[0, 1])],
            [figure.add_subplot(grid[1, 0]), figure.add_subplot(lifecycle_grid[0, 0], axes_class=HostAxes)],
        ],
        dtype=object,
    )
    figure.get_layout_engine().set(w_pad=0.07, h_pad=0.08, wspace=0.04, hspace=0.07)
    figure.suptitle("LPS atlas v5.4.2 (1940–2025)", fontsize=19, fontweight=500)

    # (a) Each physical event contributes at most once to any 1-degree cell.
    ax = axes[0, 0]
    ax.set_facecolor(SEA)
    draw_geography(ax, geography)
    lon_edges = np.arange(52, 109, 1)
    lat_edges = np.arange(-4, 37, 1)
    density = unique_track_density(frame, lon_edges, lat_edges)
    density = np.ma.masked_where(density == 0, density)
    colour_map = mpl.colors.LinearSegmentedColormap.from_list(
        "atlas_density", ["#dbe8f2", "#9bbdd3", ATLAS_BLUE, INDIGO, "#35204e"]
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
    ax.set(xlim=(56, 104), ylim=(-4, 36), xlabel="Longitude (°E)", ylabel="Latitude (°N)")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("(a) Track density", loc="left")
    style_axis(ax)
    colour_bar = figure.colorbar(mesh, ax=ax, location="right", fraction=0.036, pad=0.025)
    colour_bar.set_label("Tracks per 1° cell")
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

    # (c) Physical intensity relationship at one-point-per-event grain.
    ax = axes[1, 0]
    for category, (label, colour) in enumerate(zip(CLASS_LABELS, CLASS_COLOURS), start=1):
        subset = summary[summary["peak_class"] == category]
        ax.scatter(
            subset["peak_wind"],
            subset["peak_deficit"],
            s=12 if category < 5 else 24,
            color=colour,
            alpha=0.55 if category < 5 else 0.82,
            linewidths=0,
            label=label,
        )
    ax.set(xlabel=r"Peak maximum wind (m s$^{-1}$)", ylabel="Peak pressure deficit (hPa)")
    ax.set_title("(c) Peak wind and pressure deficit", loc="left")
    ax.legend(ncol=3, loc="upper left", handletextpad=0.35, columnspacing=1.0)
    style_axis(ax)

    # (d) Shared life-fraction x-axis with a separate physical y-scale per variable.
    lifecycle_specs = [
        ("pressure_deficit_hpa", "Pressure deficit (hPa)", MADDER, "-", "left", "%.1f"),
        ("precip_24hr", "24 h precipitation (mm)", ATLAS_BLUE, "--", "right", "%.0f"),
        ("rh850_mean_pct", "RH850 (%)", PEACOCK, ":", "right2", "%.1f"),
        ("max_vort_smoothed", r"Vorticity (10$^{-5}$ s$^{-1}$)", INDIGO, "-.", "left2", "%.1f"),
    ]
    host = axes[1, 1]
    rain_axis = host.twinx()
    rh_axis = host.twinx()
    vort_axis = host.twinx()
    rh_axis.axis["right2"] = rh_axis.new_fixed_axis(loc="right", offset=(52, 0))
    rh_axis.axis["right2"].toggle(all=True)
    rh_axis.axis["right"].set_visible(False)
    vort_axis.axis["left2"] = vort_axis.new_fixed_axis(loc="left", offset=(-52, 0))
    vort_axis.axis["left2"].toggle(all=True)
    vort_axis.axis["right"].set_visible(False)
    parasites = [host, rain_axis, rh_axis, vort_axis]
    host.axis["top"].set_visible(False)
    host.axis["right"].set_visible(False)
    for parasite in parasites[1:]:
        parasite.axis["top"].set_visible(False)
        parasite.axis["bottom"].set_visible(False)
        parasite.axis["left"].set_visible(False)
    for (metric, label, colour, line_style, axis_name, number_format), ax in zip(lifecycle_specs, parasites):
        values = lifecycle[metric]
        x = values["life_fraction"].to_numpy(dtype=float)
        median = values["median"].to_numpy(dtype=float)
        ax.plot(x, median, color=colour, linewidth=2.35, linestyle=line_style)
        span = np.nanmax(median) - np.nanmin(median)
        padding = max(0.35, span * 0.18)
        ax.set_ylim(np.nanmin(median) - padding, np.nanmax(median) + padding)
        ax.yaxis.set_major_locator(mpl.ticker.MaxNLocator(nbins=4))
        ax.yaxis.set_major_formatter(mpl.ticker.FormatStrFormatter(number_format))
        display_axis = ax.axis[axis_name]
        display_axis.toggle(all=True)
        display_axis.label.set_text(label)
        display_axis.label.set_color(colour)
        display_axis.major_ticklabels.set_color(colour)
        display_axis.major_ticks.set_color(colour)
        display_axis.line.set_color(colour)
    host.set(xlim=(0, 100), xlabel="Fraction of event lifetime (%)")
    host.set_title("(d) Median lifecycle", loc="left")
    host.grid(True, color=GRID, linewidth=0.65, alpha=0.62)
    legend_handles = [
        Line2D([0], [0], color=colour, linewidth=2.35, linestyle=line_style, label=label.split(" (")[0])
        for unused, label, colour, line_style, unused_axis, unused_format in lifecycle_specs
    ]
    host.legend(handles=legend_handles, loc="upper left", ncol=2, columnspacing=1.1, handlelength=2.2)

    return figure


def main() -> None:
    args = parse_args()
    configure_style(args.font)
    frame = load_data(args.data)
    summary = event_summary(frame)
    if len(summary) != 2_980 or not summary.index.is_unique:
        raise ValueError(f"Expected 2,980 unique physical events, found {len(summary):,}")
    required = summary[["peak_wind", "peak_deficit", "peak_class"]]
    if not np.isfinite(required.to_numpy(dtype=float)).all():
        raise ValueError("Non-finite event summary values would make the figure incomplete")
    if (summary[["peak_wind", "peak_deficit"]] < 0).any().any() or not summary["peak_class"].between(1, 6).all():
        raise ValueError("Event intensity values or peak classes are outside their valid domains")
    geography = load_geography(args.core)
    figure = build_figure(frame, summary, geography)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=190, metadata={"Software": "matplotlib", "Title": "LPS v5.4.2 track diagnostics"})
    plt.close(figure)
    print(f"Wrote {args.output} from {len(summary):,} physical events and {len(frame):,} hourly positions")


if __name__ == "__main__":
    main()
