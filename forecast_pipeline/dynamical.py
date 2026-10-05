"""Dynamical.org source bridge into the existing frozen forecast tracker."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from .dynamical_reader import DATASETS, LEVELS, TRACKABLE_START
from .forecast_core import GRID_LATS, GRID_LONS, cycle_id, parse_cycle, relative_vorticity_x1e5, utc_now
from .sources import BaseAdapter, DownloadError, EcmwfAdapter, MODEL_DEFINITIONS, available_forecast_steps
from .v56_tracking import track_forecast_member

LOGGER = logging.getLogger("mla.forecast.dynamical")
ROOT = Path(__file__).resolve().parents[1]


def reader_python() -> str:
    return os.environ.get("LPS_DYNAMICAL_PYTHON", str(ROOT / ".forecast-envs/dynamical/bin/python"))


def run_reader(*args: str, timeout: int = 1800) -> dict:
    result = subprocess.run(
        [reader_python(), "-m", "forecast_pipeline.dynamical_reader", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if result.returncode:
        raise DownloadError(f"Dynamical reader failed: {result.stderr[-2000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def load_arrays(path: Path, model: str, cycle: str, member: str, steps: list[int]):
    with np.load(path, allow_pickle=False) as data:
        metadata = json.loads(str(data["metadata"]))
        expected = {"schema": "mla-dynamical-member-v1", "dataset": DATASETS[model],
                    "model": model, "cycle": cycle, "member": member, "steps": steps}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ValueError("Dynamical cache identity/lead axis mismatch")
        if not np.array_equal(data["latitude"], GRID_LATS) or not np.array_equal(data["longitude"], GRID_LONS):
            raise ValueError("Dynamical cache has a different atlas grid")
        keys = ["mslp", "u10", "v10", "rain"] + [f"{component}{level}" for level in LEVELS for component in ("u", "v")]
        arrays = {key: np.asarray(data[key], dtype=np.float32) for key in keys}
    shape = (len(steps), len(GRID_LATS), len(GRID_LONS))
    if any(value.shape != shape for value in arrays.values()):
        raise ValueError("Dynamical cache has incorrect field dimensions")
    if any(not np.isfinite(value).all() for value in arrays.values()):
        raise ValueError("Dynamical cache contains missing fields")
    if np.min(np.diff(arrays["rain"], axis=0)) < -.05:
        raise ValueError("Dynamical cached accumulation decreases")
    return arrays, metadata


class DynamicalAdapter(EcmwfAdapter):
    """Use the established member tracking/ensemble aggregation implementation."""

    def __init__(self, model: str, workers: int = 4):
        if model not in DATASETS:
            raise ValueError(model)
        BaseAdapter.__init__(self, workers=workers)
        self.definition = MODEL_DEFINITIONS[model]
        self._inventory = None

    def inventory(self):
        if self._inventory is None:
            self._inventory = run_reader("--model", self.definition.id, "--inventory", timeout=300)
        return self._inventory

    def cycle_complete(self, cycle: datetime, horizon: int) -> bool:
        if cycle_id(cycle) < TRACKABLE_START[self.definition.id]:
            return False
        inventory = self.inventory()
        cycles = {str(value)[:19] for value in inventory["cycles"]}
        return (
            cycle.replace(tzinfo=None).isoformat(timespec="seconds") in cycles
            and horizon in inventory["steps"]
            and set(LEVELS).issubset(inventory["pressure_levels"])
        )

    def resolve_available_cycle(self, requested: str):
        if requested != "latest":
            return super().resolve_available_cycle(requested)
        now = utc_now()
        candidates = sorted((datetime.fromisoformat(str(value)[:19]).replace(tzinfo=UTC)
                             for value in self.inventory()["cycles"]), reverse=True)
        # Metadata may be preallocated: reject future/stale cycles, and actual
        # field completeness is checked again for every member before publication.
        for cycle in candidates:
            if now - timedelta(hours=96) <= cycle <= now - timedelta(hours=8):
                steps = available_forecast_steps(self.definition.id, cycle)
                if self.cycle_complete(cycle, steps[-1]):
                    return cycle, steps
        raise DownloadError(f"No recent {self.definition.label} cycle in dynamical.org")

    def _indexes_for_steps(self, cycle, steps):
        return None

    def _member_ids(self, member_limit):
        values = ["det"] if self.definition.id == "aifs" else ["c00"] + [f"p{i:02d}" for i in range(1, 31)]
        return values if member_limit is None else values[:max(1, member_limit)]

    def _load_member(self, cycle, steps, member, indexes=None):
        model = self.definition.id
        steps = [int(value) for value in steps]
        LOGGER.info("Reading %s %s %s to +%d h from dynamical.org", model, cycle_id(cycle), member, steps[-1])
        cache = Path(os.environ.get("LPS_DYNAMICAL_INPUT_CACHE", str(ROOT / ".forecast-runs/dynamical-inputs")))
        path = cache / model / cycle_id(cycle) / f"{member}.npz"
        if not path.exists():
            run_reader("--model", model, "--cycle", cycle_id(cycle), "--member", member,
                       "--horizon", str(steps[-1]), "--output", str(path), "--workers", "4")
        arrays, metadata = load_arrays(path, model, cycle_id(cycle), member, steps)
        winds = {level: (arrays[f"u{level}"], arrays[f"v{level}"]) for level in LEVELS}
        vort = {level: np.stack([relative_vorticity_x1e5(u, v) for u, v in zip(*pair)])
                for level, pair in winds.items()}
        tracking = track_forecast_member(
            cycle=cycle, steps=steps, member=member,
            role="deterministic" if member == "det" else "control" if member == "c00" else "perturbed",
            mslp_hpa=arrays["mslp"], vorticity_by_level=vort, wind_by_level=winds,
            wind_10m=(arrays["u10"], arrays["v10"]), precipitation_cumulative_mm=arrays["rain"],
        )
        # The compact tracked result supersedes this disposable input cache.
        # Failed tracking retains it, avoiding another remote download on retry.
        path.unlink()
        return {"member": member, "tracks": tracking.tracks, "vorticity": vort[850],
                "precipitation": arrays["rain"], "tracking_qa": {
                    "member": member, "detector_candidates": tracking.detector_candidates,
                    "linker": tracking.linker_summary, "crosscheck": tracking.qa_crosscheck,
                    "source_validation": metadata,
                }}

    def _payload(self, *args, **kwargs):
        payload = super()._payload(*args, **kwargs)
        payload["source"].update({
            "service": "dynamical.org virtual GRIB archive",
            "url": f"https://dynamical.org/catalog/{DATASETS[self.definition.id]}/",
            "dataset": DATASETS[self.definition.id],
            "retrieval": "STAC-discovered Icechunk; original-provider GRIB ranges; common 1-degree atlas grid",
        })
        if self.definition.id == "gefs-extended":
            payload["warnings"].append("Beyond two weeks, use ensemble activity probabilities rather than individual storm paths.")
        return payload


class AifsHybridAdapter(EcmwfAdapter):
    """Preserve the fast live feed, use dynamical.org for historical cycles."""

    def __init__(self, workers: int = 4):
        super().__init__("aifs", workers=workers)
        self.archive = DynamicalAdapter("aifs", workers=workers)

    def historical(self, requested: str) -> bool:
        return requested != "latest" and parse_cycle(requested) < utc_now() - timedelta(days=4)

    def resolve_available_cycle(self, requested):
        if self.historical(requested):
            return self.archive.resolve_available_cycle(requested)
        return super().resolve_available_cycle(requested)

    def build(self, requested, steps, member_limit=None):
        if self.historical(requested):
            return self.archive.build(requested, steps, member_limit)
        return super().build(requested, steps, member_limit)
