#!/usr/bin/env python3
"""Apply the frozen ERA5 detector-space physical-event gate to native tracks.

This is the part of the v5.6 release gate that can be evaluated consistently
from every standardised reanalysis detector table.  It deliberately does not
assign ERA5-equivalent intensity: that requires source-specific final-centre
wind and closed-isobar recomputation, which is not present in linker output.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


SELECTION_SCHEMA = "lps-atlas-reanalysis-physical-selection-v1"
THRESHOLDS = {
    "minimum_observed_span_hours": 30.0,
    "minimum_observed_positions": 12,
    "minimum_qualifying_positions": 3,
    "minimum_category": 1.0,
    "minimum_candidate_quality": 5.832555,
    "minimum_centre_score": 6.446782,
    "minimum_vorticity": 7.036606,
    "minimum_pressure_deficit_hpa": 3.495224,
    "maximum_heat_low_score": 0.544212,
    "release_domain": [65.0, 100.0, 0.0, 32.0],
}
REQUIRED_COLUMNS = {
    "track_id",
    "time",
    "lon_detected",
    "lat_detected",
    "position_source",
    "imd_category_raw",
    "candidate_quality",
    "centre_score",
    "max_vort_smoothed",
    "pressure_deficit_hpa",
    "heat_low_score",
}


def validate_selection_columns(frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"source-native linker table lacks physical-selection columns: {missing}")


def physical_track_passes(group: pd.DataFrame) -> bool:
    """Return whether one linker identity passes the frozen detector-space gate."""

    validate_selection_columns(group)
    times = pd.to_datetime(group["time"], utc=True, errors="coerce")
    observed = group["position_source"].astype(str).str.lower().eq("observed") & times.notna()
    observed_times = times.loc[observed]
    if observed_times.empty:
        return False
    observed_span_hours = (observed_times.max() - observed_times.min()).total_seconds() / 3600.0
    if observed_span_hours < THRESHOLDS["minimum_observed_span_hours"]:
        return False
    if observed_times.nunique() < THRESHOLDS["minimum_observed_positions"]:
        return False

    west, east, south, north = THRESHOLDS["release_domain"]
    numeric = {
        name: pd.to_numeric(group[name], errors="coerce")
        for name in (
            "lon_detected",
            "lat_detected",
            "imd_category_raw",
            "candidate_quality",
            "centre_score",
            "max_vort_smoothed",
            "pressure_deficit_hpa",
            "heat_low_score",
        )
    }
    qualifying = (
        observed
        & numeric["lon_detected"].between(west, east)
        & numeric["lat_detected"].between(south, north)
        & numeric["imd_category_raw"].ge(THRESHOLDS["minimum_category"])
        & numeric["candidate_quality"].ge(THRESHOLDS["minimum_candidate_quality"])
        & numeric["centre_score"].ge(THRESHOLDS["minimum_centre_score"])
        & numeric["max_vort_smoothed"].ge(THRESHOLDS["minimum_vorticity"])
        & numeric["pressure_deficit_hpa"].ge(THRESHOLDS["minimum_pressure_deficit_hpa"])
        & numeric["heat_low_score"].le(THRESHOLDS["maximum_heat_low_score"])
    )
    return times.loc[qualifying].nunique() >= THRESHOLDS["minimum_qualifying_positions"]


def select_physical_tracks(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Filter a complete linker table and return an auditable selection summary."""

    validate_selection_columns(frame)
    working = frame
    if "reject_reason" in working:
        working = working.loc[working["reject_reason"].astype(str).eq("accepted")]
    linker_tracks = int(working["track_id"].nunique())
    selected_ids = {
        str(track_id)
        for track_id, group in working.groupby("track_id", sort=False)
        if physical_track_passes(group)
    }
    selected = working.loc[working["track_id"].astype(str).isin(selected_ids)].copy()
    summary = {
        "schema": SELECTION_SCHEMA,
        "basis": "Frozen v5.6 detector-space physical-event thresholds",
        "linker_tracks": linker_tracks,
        "selected_tracks": len(selected_ids),
        "rejected_tracks": linker_tracks - len(selected_ids),
        "thresholds": THRESHOLDS,
        "limitations": (
            "Uses source-native detector diagnostics and linker geometry; it does not apply "
            "ERA5-only final-centre resampling, closed-isobar classification or circulation-wind intensity."
        ),
    }
    return selected, summary
