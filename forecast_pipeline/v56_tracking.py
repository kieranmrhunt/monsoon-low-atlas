#!/usr/bin/env python3
"""Run forecast fields through the frozen v5.6 detector and linker.

The v5.6 catalogue retained the v5.4.2 candidate detector and continuity
linker unchanged.  This module is a deliberately thin in-memory adapter around
those exact routines.  Forecast fields arrive at native six-hourly lead times;
continuous dynamical fields are linearly interpolated to the linker's hourly
clock, while accumulated precipitation is converted to hourly increments.

Forecast tracks are guidance, not additions to the ERA5 catalogue. Tracks
whose life is observable within the forecast use the frozen v5.6 physical-event
gate. Only tracks touching an initialization or horizon edge may use a scaled
version of its support-duration requirements, after retaining the same strong
release-domain evidence requirement.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .forecast_core import (
    GRID_LATS,
    GRID_LONS,
    detect_candidates as qa_detect_candidates,
    link_candidates as qa_link_candidates,
)


TRACKER_ROOT = Path(__file__).resolve().parents[2] / "lps-v5.3-continuity-framework"
PARAMETER_PATH = TRACKER_ROOT / "params" / "lps_v5.4.2_liberal_poststitch_identity.json"

if str(TRACKER_ROOT) not in sys.path:
    sys.path.insert(0, str(TRACKER_ROOT))

from lps53_detect import (  # noqa: E402
    apply_pressure_level_validity,
    build_candidate_row,
    candidates_in_domain,
    compute_score_v53,
    object_candidates_for_frame,
    select_diverse_candidates,
    with_v53_defaults,
)
from lps53_link import link_candidate_dataframe  # noqa: E402


FORECAST_MINIMUM_SUPPORT_HOURS = 18
RETROSPECTIVE_MINIMUM_PHYSICAL_HOURS = 12
RETROSPECTIVE_MINIMUM_CONTIGUOUS_PHYSICAL_HOURS = 12
RETROSPECTIVE_MINIMUM_OBSERVED_SPAN_HOURS = 72
RETROSPECTIVE_MINIMUM_OBSERVED_POSITIONS = 36
MINIMUM_RELEASE_DOMAIN_POSITIONS = 3
FORECAST_COALESCENCE_DISTANCE_KM = 75.0
FORECAST_COALESCENCE_MINIMUM_HOURS = 6


def parameter_sha256() -> str:
    digest = hashlib.sha256()
    with PARAMETER_PATH.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_parameters() -> dict[str, Any]:
    return json.loads(PARAMETER_PATH.read_text(encoding="utf-8"))


def _hourly_axis(steps: Sequence[int]) -> np.ndarray:
    values = np.asarray(steps, dtype=int)
    if values.ndim != 1 or not len(values):
        raise ValueError("forecast steps must be a non-empty one-dimensional sequence")
    if values[0] < 0 or np.any(np.diff(values) <= 0):
        raise ValueError("forecast steps must be non-negative and increase strictly")
    if np.any(np.diff(values) > 6):
        raise ValueError("v5.6 forecast tracking refuses native gaps longer than six hours")
    return np.arange(int(values[0]), int(values[-1]) + 1, dtype=int)


def interpolate_hourly(values: np.ndarray, steps: Sequence[int]) -> np.ndarray:
    """Linearly interpolate a time-first array without flattening the grid."""

    source = np.asarray(values, dtype=np.float32)
    native = np.asarray(steps, dtype=int)
    if source.shape[0] != len(native):
        raise ValueError(f"field has {source.shape[0]} frames for {len(native)} steps")
    target = _hourly_axis(native)
    output = np.empty((len(target),) + source.shape[1:], dtype=np.float32)
    offset = int(native[0])
    for index in range(len(native) - 1):
        start = int(native[index])
        stop = int(native[index + 1])
        width = stop - start
        for hour in range(start, stop):
            weight = np.float32((hour - start) / width)
            output[hour - offset] = source[index] * (1.0 - weight) + source[index + 1] * weight
    output[int(native[-1]) - offset] = source[-1]
    return output


def accumulated_to_hourly_precipitation(
    cumulative: np.ndarray,
    steps: Sequence[int],
    interval_valid: np.ndarray | None = None,
) -> np.ndarray:
    """Convert accumulated model precipitation to hourly increments.

    The initial frame is missing rather than an invented dry hour.  Thus the
    detector's 24-hour precipitation component becomes valid at +24 h.  When
    a provider omits a native accumulation interval, ``interval_valid`` marks
    the frame ending that interval false and each corresponding hourly
    increment remains missing rather than being interpreted as zero rain.
    """

    interpolated = interpolate_hourly(np.maximum(cumulative, 0.0), steps)
    increments = np.empty_like(interpolated, dtype=np.float32)
    increments[0] = np.nan
    increments[1:] = np.maximum(interpolated[1:] - interpolated[:-1], 0.0)
    if interval_valid is not None:
        source_valid = np.asarray(interval_valid, dtype=bool)
        cumulative_shape = np.asarray(cumulative).shape
        if source_valid.shape != cumulative_shape:
            try:
                source_valid = np.broadcast_to(source_valid, cumulative_shape)
            except ValueError as error:
                raise ValueError(
                    "precipitation interval-valid mask cannot be broadcast to the cumulative field"
                ) from error
        native = np.asarray(steps, dtype=int)
        offset = int(native[0])
        for index in range(1, len(native)):
            start = int(native[index - 1]) - offset + 1
            stop = int(native[index]) - offset + 1
            increments[start:stop] = np.where(
                source_valid[index], increments[start:stop], np.nan
            )
    return increments


def rolling_24h(increments: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(increments, dtype=np.float32)
    valid = np.isfinite(source)
    values = np.where(valid, source, 0.0).astype(np.float64)
    sums = np.concatenate(
        [np.zeros((1,) + source.shape[1:], dtype=np.float64), np.cumsum(values, axis=0)],
        axis=0,
    )
    counts = np.concatenate(
        [np.zeros((1,) + source.shape[1:], dtype=np.int16), np.cumsum(valid, axis=0, dtype=np.int16)],
        axis=0,
    )
    output = np.full_like(source, np.nan, dtype=np.float32)
    valid_count = np.zeros_like(source, dtype=np.uint8)
    for index in range(source.shape[0]):
        end = index + 1
        start = max(0, end - 24)
        total = sums[end] - sums[start]
        count = counts[end] - counts[start]
        output[index] = np.where(count >= 24, total, np.nan).astype(np.float32)
        valid_count[index] = np.minimum(count, 255).astype(np.uint8)
    return output, valid_count


@dataclass(frozen=True)
class ForecastTrackingResult:
    tracks: list[dict[str, Any]]
    detector_candidates: int
    linker_summary: dict[str, Any]
    qa_crosscheck: dict[str, Any]


def _candidate_frame(
    arrays: dict[str, np.ndarray],
    score: np.ndarray,
    times: pd.DatetimeIndex,
    parameters: Mapping[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    candidate_id = 0
    for time_index, timestamp in enumerate(times):
        objects = object_candidates_for_frame(
            score[time_index],
            arrays["score_vort_band"][time_index],
            GRID_LATS,
            GRID_LONS,
            parameters,
        )
        domain_objects = candidates_in_domain(objects, parameters)
        selected = select_diverse_candidates(domain_objects, parameters)
        for rank, item in enumerate(selected, start=1):
            rows.append(
                build_candidate_row(
                    item,
                    candidate_id,
                    timestamp.strftime("%Y%m"),
                    timestamp,
                    time_index,
                    arrays,
                    score,
                    GRID_LATS,
                    GRID_LONS,
                    parameters,
                    len(objects),
                    len(domain_objects),
                    len(selected),
                    rank,
                )
            )
            candidate_id += 1
    return pd.DataFrame(rows)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _longest_true_run(mask: np.ndarray, steps: np.ndarray) -> int:
    """Return inclusive hours in the longest hourly physical-support run."""

    best = current = 0
    previous: int | None = None
    for value, step in zip(mask.astype(bool), steps.astype(int), strict=True):
        if value:
            current = current + 1 if previous is not None and step - previous <= 1 else 1
            best = max(best, current)
            previous = int(step)
        else:
            current = 0
            previous = None
    return best


def _forecast_haversine_km(
    first_lon: np.ndarray,
    first_lat: np.ndarray,
    second_lon: np.ndarray,
    second_lat: np.ndarray,
) -> np.ndarray:
    """Vectorised great-circle separation for forecast-track QA."""

    first_lat_rad = np.radians(np.asarray(first_lat, dtype=float))
    second_lat_rad = np.radians(np.asarray(second_lat, dtype=float))
    delta_lat = second_lat_rad - first_lat_rad
    delta_lon = np.radians(np.asarray(second_lon, dtype=float) - np.asarray(first_lon, dtype=float))
    value = (
        np.sin(delta_lat / 2.0) ** 2
        + np.cos(first_lat_rad) * np.cos(second_lat_rad) * np.sin(delta_lon / 2.0) ** 2
    )
    return 12_742.0 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))


def _sustained_coalescence(
    first: pd.DataFrame,
    second: pd.DataFrame,
) -> tuple[pd.Timestamp, int] | None:
    """Return the first sustained same-member coalescence.

    Two features can be physically distinct for much of a forecast and then
    merge. At the one-degree tracking resolution, two centres from the same
    model member that remain within 75 km for six consecutive hourly analyses
    are not independently resolvable. The first identity is therefore kept
    after that sustained encounter. One-off crossings remain distinct.
    """

    first_positions = first[["time", "lon", "lat"]].copy()
    second_positions = second[["time", "lon", "lat"]].copy()
    first_positions["time"] = pd.to_datetime(first_positions["time"], utc=True)
    second_positions["time"] = pd.to_datetime(second_positions["time"], utc=True)
    common = first_positions.merge(
        second_positions,
        on="time",
        how="inner",
        suffixes=("_first", "_second"),
    ).sort_values("time")
    if common.empty:
        return None
    distance = _forecast_haversine_km(
        common["lon_first"].to_numpy(),
        common["lat_first"].to_numpy(),
        common["lon_second"].to_numpy(),
        common["lat_second"].to_numpy(),
    )
    close = distance <= FORECAST_COALESCENCE_DISTANCE_KM
    times = pd.DatetimeIndex(common["time"])
    run_start: int | None = None
    for index, is_close in enumerate(close):
        contiguous = (
            index == 0
            or math.isclose(
                (times[index] - times[index - 1]).total_seconds() / 3600.0,
                1.0,
            )
        )
        if not is_close:
            run_start = None
            continue
        if run_start is None or not contiguous:
            run_start = index
        duration_hours = int(
            round((times[index] - times[run_start]).total_seconds() / 3600.0)
        ) + 1
        if duration_hours >= FORECAST_COALESCENCE_MINIMUM_HOURS:
            return pd.Timestamp(times[run_start]), duration_hours
    return None


def merge_persistent_same_member_coalescences(
    accepted: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Collapse duplicate paths after same-member forecast features merge.

    The earlier identity is retained.  Within the merged tail, observed rows
    outrank interpolated rows so the surviving path follows the actual detected
    centre instead of preserving alternating interpolation artefacts.
    """

    if accepted.empty or "track_id" not in accepted:
        return accepted.copy(), []
    frame = accepted.copy()
    original_columns = list(frame.columns)
    audit: list[dict[str, Any]] = []
    while True:
        groups = {
            track_id: group.sort_values("time").copy()
            for track_id, group in frame.groupby("track_id", sort=False)
        }
        match: tuple[Any, Any, pd.Timestamp, int] | None = None
        identifiers = list(groups)
        for first_index, first_id in enumerate(identifiers):
            for second_id in identifiers[first_index + 1:]:
                result = _sustained_coalescence(groups[first_id], groups[second_id])
                if result is not None:
                    match = (first_id, second_id, result[0], result[1])
                    break
            if match is not None:
                break
        if match is None:
            break
        first_id, second_id, merge_time, close_hours = match
        first_group, second_group = groups[first_id], groups[second_id]

        def identity_rank(track_id: Any, group: pd.DataFrame) -> tuple[pd.Timestamp, int, str]:
            observed = group.get("position_source", pd.Series("observed", index=group.index))
            return (
                pd.to_datetime(group["time"], utc=True).min(),
                -int(observed.astype(str).str.lower().eq("observed").sum()),
                str(track_id),
            )

        if identity_rank(first_id, first_group) <= identity_rank(second_id, second_group):
            keeper, terminated = first_id, second_id
        else:
            keeper, terminated = second_id, first_id
        normalized_time = pd.to_datetime(frame["time"], utc=True)
        tail_mask = frame["track_id"].isin([keeper, terminated]) & normalized_time.ge(merge_time)
        tail = frame.loc[tail_mask].copy()
        position_source = tail.get("position_source", pd.Series("observed", index=tail.index))
        tail["_observed_priority"] = position_source.astype(str).str.lower().eq("observed").astype(int)
        score_column = next(
            (name for name in ("score_v53", "score", "max_vort_smoothed") if name in tail),
            None,
        )
        tail["_score_priority"] = (
            pd.to_numeric(tail[score_column], errors="coerce").fillna(-math.inf)
            if score_column is not None
            else 0.0
        )
        tail["_keeper_priority"] = tail["track_id"].eq(keeper).astype(int)
        tail["_normalized_time"] = pd.to_datetime(tail["time"], utc=True)
        tail = (
            tail.sort_values(
                ["_normalized_time", "_observed_priority", "_score_priority", "_keeper_priority"],
                ascending=[True, False, False, False],
                kind="stable",
            )
            .drop_duplicates("_normalized_time", keep="first")
        )
        tail["track_id"] = keeper
        frame = pd.concat(
            [frame.loc[~tail_mask, original_columns], tail.loc[:, original_columns]],
            ignore_index=True,
        ).sort_values(["track_id", "time"], kind="stable")
        audit.append({
            "kept_track_id": str(keeper),
            "terminated_track_id": str(terminated),
            "merge_time_utc": merge_time.isoformat().replace("+00:00", "Z"),
            "qualifying_close_run_hours": int(close_hours),
            "distance_threshold_km": FORECAST_COALESCENCE_DISTANCE_KM,
        })
    return frame.reset_index(drop=True), audit


def _published_tracks(
    accepted: pd.DataFrame,
    cycle: datetime,
    member: str,
    role: str,
    forecast_horizon: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    cycle_naive = pd.Timestamp(cycle.astimezone(UTC).replace(tzinfo=None))
    if accepted.empty:
        return output
    for unused_track_id, group in accepted.groupby("track_id", sort=False):
        group = group.sort_values("time").copy()
        times = pd.to_datetime(group["time"], utc=True).dt.tz_convert(None)
        steps = np.rint((times - cycle_naive).dt.total_seconds().to_numpy() / 3600.0).astype(int)
        keep = (steps >= 0)
        group = group.loc[keep].copy()
        steps = steps[keep]
        if group.empty:
            continue
        observed = group["position_source"].astype(str).str.lower().eq("observed").to_numpy()
        observed_steps = steps[observed]
        support = int(observed_steps[-1] - observed_steps[0] + 1) if len(observed_steps) else 0
        if support < FORECAST_MINIMUM_SUPPORT_HOURS:
            continue
        observed_frame = group.loc[observed]
        observed_vorticity = _numeric(observed_frame, "max_vort_smoothed")
        observed_pressure = _numeric(observed_frame, "pressure_deficit_hpa")
        observed_category = _numeric(observed_frame, "imd_category")
        if observed_category.isna().all():
            observed_category = _numeric(observed_frame, "imd_category_raw")
        physical = (
            observed_vorticity.ge(5.0)
            & observed_pressure.ge(2.0)
            & _numeric(observed_frame, "closed_isobars_2hpa_local").ge(1.0)
            & _numeric(observed_frame, "vort_peak_distance_km").le(225.0)
            & _numeric(observed_frame, "mslp_min_distance_km").le(250.0)
            & _numeric(observed_frame, "vort_mslp_extrema_separation_km").le(300.0)
        ).fillna(False)
        release_domain = (
            _numeric(observed_frame, "lon").between(65.0, 100.0)
            & _numeric(observed_frame, "lat").between(0.0, 32.0)
            & observed_category.ge(1.0)
            & observed_vorticity.ge(7.036606)
            & observed_pressure.ge(3.495224)
            & _numeric(observed_frame, "heat_low_score").le(0.544212)
        ).fillna(False)
        observed_span = int(observed_steps[-1] - observed_steps[0])
        physical_hours = int(physical.sum())
        contiguous_physical = _longest_true_run(physical.to_numpy(bool), observed_steps)
        release_positions = int(release_domain.sum())
        retrospective_gate = (
            physical_hours >= RETROSPECTIVE_MINIMUM_PHYSICAL_HOURS
            and contiguous_physical >= RETROSPECTIVE_MINIMUM_CONTIGUOUS_PHYSICAL_HOURS
            and observed_span >= RETROSPECTIVE_MINIMUM_OBSERVED_SPAN_HOURS
            and len(observed_steps) >= RETROSPECTIVE_MINIMUM_OBSERVED_POSITIONS
            and release_positions >= MINIMUM_RELEASE_DOMAIN_POSITIONS
        )
        touches_window_edge = bool(
            observed_steps[0] <= 6 or observed_steps[-1] >= forecast_horizon - 6
        )
        scaled_physical = max(
            3,
            min(
                RETROSPECTIVE_MINIMUM_PHYSICAL_HOURS,
                math.ceil(RETROSPECTIVE_MINIMUM_PHYSICAL_HOURS * support / 72.0),
            ),
        )
        forecast_edge_gate = (
            touches_window_edge
            and support >= FORECAST_MINIMUM_SUPPORT_HOURS
            and physical_hours >= scaled_physical
            and contiguous_physical >= scaled_physical
            and release_positions >= MINIMUM_RELEASE_DOMAIN_POSITIONS
        )
        if not (retrospective_gate or forecast_edge_gate):
            continue
        points: list[list[Any]] = []
        for step, (_, row) in zip(steps, group.iterrows(), strict=True):
            points.append([
                int(step),
                round(_finite(row.get("lon")), 3),
                round(_finite(row.get("lat")), 3),
                round(_finite(row.get("max_vort_smoothed")), 2),
                round(_finite(row.get("pressure_deficit_hpa")), 2),
                round(_finite(row.get("min_mslp"), 9999.0), 1),
                int(_finite(row.get("imd_category"), _finite(row.get("imd_category_raw")))),
                "o" if str(row.get("position_source", "observed")).lower() == "observed" else "i",
            ])
        if not points:
            continue
        output.append({
            "member": member,
            "role": role,
            "points": points,
            "start_step": int(points[0][0]),
            "end_step": int(points[-1][0]),
            "observed_support_hours": support,
            "physical_support_positions": physical_hours,
            "contiguous_physical_support_hours": contiguous_physical,
            "release_domain_support_positions": release_positions,
            "publication_gate": "retrospective-equivalent" if retrospective_gate else "forecast-window-edge",
            "max_vorticity": round(max(point[3] for point in points), 2),
            "max_pressure_deficit": round(max(point[4] for point in points), 2),
            "minimum_mslp": round(min(point[5] for point in points), 1),
            "maximum_provisional_category": max(point[6] for point in points),
        })
    output.sort(key=lambda item: (-item["observed_support_hours"], -item["max_vorticity"], item["start_step"]))
    for number, item in enumerate(output, start=1):
        item["id"] = f"{member}-T{number:02d}"
    return output


def _qa_tracker(
    native_mslp: np.ndarray,
    native_vo850: np.ndarray,
    steps: Sequence[int],
    member: str,
    role: str,
) -> dict[str, Any]:
    frames = {
        int(step): qa_detect_candidates(int(step), native_mslp[index], native_vo850[index])
        for index, step in enumerate(steps)
    }
    tracks = qa_link_candidates(frames, member, role)
    return {
        "name": "model-neutral-vorticity-pressure-crosscheck",
        "publication_use": False,
        "track_count": len(tracks),
        "candidate_count": int(sum(len(values) for values in frames.values())),
    }


def track_forecast_member(
    *,
    cycle: datetime,
    steps: Sequence[int],
    member: str,
    role: str,
    mslp_hpa: np.ndarray,
    vorticity_by_level: Mapping[int, np.ndarray],
    wind_by_level: Mapping[int, tuple[np.ndarray, np.ndarray]],
    wind_10m: tuple[np.ndarray, np.ndarray],
    precipitation_cumulative_mm: np.ndarray,
    precipitation_interval_valid: np.ndarray | None = None,
) -> ForecastTrackingResult:
    """Return tracks produced by the catalogue detector/linker, plus QA."""

    required_levels = {850, 700, 500}
    if set(vorticity_by_level) != required_levels or set(wind_by_level) != required_levels:
        raise ValueError("v5.6 forecast tracking requires 850, 700 and 500 hPa winds/vorticity")
    hourly_steps = _hourly_axis(steps)
    times = pd.date_range(
        pd.Timestamp(
            (cycle + timedelta(hours=int(hourly_steps[0]))).astimezone(UTC).replace(tzinfo=None)
        ),
        periods=len(hourly_steps),
        freq="h",
    )
    vo = {level: interpolate_hourly(vorticity_by_level[level], steps) for level in sorted(required_levels)}
    winds = {
        level: (
            interpolate_hourly(wind_by_level[level][0], steps),
            interpolate_hourly(wind_by_level[level][1], steps),
        )
        for level in sorted(required_levels)
    }
    u10 = interpolate_hourly(wind_10m[0], steps)
    v10 = interpolate_hourly(wind_10m[1], steps)
    precip_1h = accumulated_to_hourly_precipitation(
        precipitation_cumulative_mm,
        steps,
        interval_valid=precipitation_interval_valid,
    )
    precip_24h, precip_counts = rolling_24h(precip_1h)
    arrays: dict[str, np.ndarray] = {
        "vo850": vo[850],
        "vo700": vo[700],
        "vo500": vo[500],
        "vort_core": (0.58 * vo[850] + 0.42 * vo[700]).astype(np.float32),
        "msl_hpa": interpolate_hourly(mslp_hpa, steps),
        "u850": winds[850][0],
        "v850": winds[850][1],
        "u700": winds[700][0],
        "v700": winds[700][1],
        "u500": winds[500][0],
        "v500": winds[500][1],
        "ws10": np.hypot(u10, v10).astype(np.float32),
        "prcp_1h": precip_1h,
        "prcp_24h": precip_24h,
        "prcp_24h_count_grid": precip_counts,
        "prcp_24h_count": np.min(precip_counts.reshape((len(times), -1)), axis=1).astype(np.int16),
    }
    parameters = load_parameters()
    detector_parameters = with_v53_defaults(parameters["detect"])
    apply_pressure_level_validity(arrays, detector_parameters)
    score = compute_score_v53(arrays, GRID_LATS, GRID_LONS, detector_parameters)
    with warnings.catch_warnings():
        # Empty local rings can occur at the declared grid edge; the detector
        # already records those diagnostics as missing and handles them safely.
        warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
        candidates = _candidate_frame(arrays, score, times, detector_parameters)
    crosscheck = _qa_tracker(mslp_hpa, vorticity_by_level[850], steps, member, role)
    if candidates.empty:
        return ForecastTrackingResult([], 0, {"accepted_tracks": 0}, crosscheck)
    linked = link_candidate_dataframe(candidates, parameters)
    accepted, coalescence_audit = merge_persistent_same_member_coalescences(linked.accepted)
    tracks = _published_tracks(accepted, cycle, member, role, int(max(steps)))
    linker_summary = dict(linked.summary)
    linker_summary["forecast_coalesced_track_tails"] = len(coalescence_audit)
    if coalescence_audit:
        linker_summary["forecast_coalescence_audit"] = coalescence_audit
    return ForecastTrackingResult(tracks, len(candidates), linker_summary, crosscheck)
