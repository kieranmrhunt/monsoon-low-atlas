#!/usr/bin/env python3
"""Plan CMA-portal recovery requests for missing UKMO/IMD/NCMRWF TIGGE cycles."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .forecast_core import atomic_write_json, iso_z, utc_now
from .sources import TIGGE_CENTRES
from .update import read_manifest


CMA_HISTORY_START = datetime(2019, 8, 7, 0, tzinfo=UTC)
TARGET_MODELS = ("tigge-ukmo", "tigge-imd", "tigge-ncmrwf")
PRESSURE_PARAMETERS = ("500;34", "500;35", "700;34", "700;35", "850;34", "850;35")
SURFACE_PARAMETERS = ("3", "4", "10", "28")


def cma_history_end(now: datetime | None = None) -> datetime:
    # The portal describes historical staging as excluding the recent two
    # months. Keep a two-day safety margin around that moving boundary.
    return (now or utc_now()).astimezone(UTC) - timedelta(days=62)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plans", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", default=",".join(TARGET_MODELS))
    parser.add_argument("--eligible-start", default=CMA_HISTORY_START.strftime("%Y%m%d%H"))
    parser.add_argument("--eligible-end", default=cma_history_end().strftime("%Y%m%d%H"))
    return parser.parse_args()


def _selected_models(value: str) -> list[str]:
    models = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(models) - set(TARGET_MODELS))
    if unknown:
        raise ValueError(f"CMA recovery is limited to {', '.join(TARGET_MODELS)}; got {', '.join(unknown)}")
    return models


def _manifest_keys(path: Path) -> set[str]:
    manifest = read_manifest(path)
    return {
        f"{item.get('model')}:{item.get('cycle')}"
        for item in manifest.get("tigge_archive", [])
    }


def _pending_items(paths: list[Path], models: set[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for path in paths:
        plan = json.loads(path.read_text(encoding="utf-8"))
        for item in plan.get("pending_cycles", []):
            model = str(item.get("model"))
            cycle = str(item.get("cycle"))
            if model in models and len(cycle) == 10:
                output[f"{model}:{cycle}"] = dict(item)
    return output


def _request_item(item: dict[str, Any], component: str) -> dict[str, Any]:
    model = str(item["model"])
    cycle = datetime.strptime(str(item["cycle"]), "%Y%m%d%H").replace(tzinfo=UTC)
    centre = TIGGE_CENTRES[model]
    first_step = int(item.get("first_step_hours", 0))
    horizon = int(item.get("horizon_hours", centre.maximum_horizon_hours))
    steps = [str(step) for step in range(first_step, horizon + 1, 6)]
    request: dict[str, Any] = {
        "lid": 3 if component == "pressure" else 4,
        "startDate": cycle.strftime("%Y-%m-%d"),
        "endDate": cycle.strftime("%Y-%m-%d"),
        "originTimes": [f"{centre.archive_origin};{cycle.hour}"],
        "steps": steps,
    }
    if component == "pressure":
        request["levelistParams"] = list(PRESSURE_PARAMETERS)
    else:
        request["params"] = list(SURFACE_PARAMETERS)
    return {
        "key": f"{model}:{cycle:%Y%m%d%H}:{component}",
        "model": model,
        "cycle": cycle.strftime("%Y%m%d%H"),
        "cycle_utc": iso_z(cycle),
        "component": component,
        "request": request,
    }


def main() -> None:
    args = parse_args()
    models = _selected_models(args.models)
    eligible_start = datetime.strptime(args.eligible_start, "%Y%m%d%H").replace(tzinfo=UTC)
    eligible_end = datetime.strptime(args.eligible_end, "%Y%m%d%H").replace(tzinfo=UTC)
    if eligible_start > eligible_end:
        raise ValueError("eligible CMA history interval is empty")
    public = _manifest_keys(args.manifest)
    pending = _pending_items(args.plans, set(models))
    eligible: list[dict[str, Any]] = []
    ineligible: list[dict[str, str]] = []
    for key, item in sorted(pending.items()):
        if key in public:
            continue
        cycle = datetime.strptime(str(item["cycle"]), "%Y%m%d%H").replace(tzinfo=UTC)
        if not eligible_start <= cycle <= eligible_end:
            ineligible.append({"model": str(item["model"]), "cycle": str(item["cycle"])})
            continue
        eligible.append(item)
    requests = [
        _request_item(item, component)
        for item in eligible
        for component in ("pressure", "surface")
    ]
    plan = {
        "schema": "mla-cma-tigge-recovery-plan-v1",
        "generated_utc": iso_z(utc_now()),
        "provider": "CMA synchronized TIGGE portal",
        "provider_url": "http://tigge.cma.cn/",
        "models": models,
        "source_plans": [str(path) for path in args.plans],
        "eligible_start_utc": iso_z(eligible_start),
        "eligible_end_utc": iso_z(eligible_end),
        "selection_policy": (
            "missing public UKMO/IMD/NCMRWF model-cycles already selected by the atlas TIGGE plans, "
            "limited to the CMA historical portal's post-August-2019, non-recent interval"
        ),
        "retrieval_policy": (
            "one pressure-level and one surface application per initialization; 500/700/850-hPa u/v, "
            "MSLP, 10-m u/v and total precipitation at every available six-hourly lead"
        ),
        "target_model_cycles": len(eligible),
        "request_count": len(requests),
        "ineligible_model_cycles": len(ineligible),
        "ineligible": ineligible,
        "requests": requests,
    }
    atomic_write_json(args.output, plan)
    print(
        f"Planned {len(requests)} CMA applications for {len(eligible)} missing model-cycles; "
        f"{len(ineligible)} cycles are outside CMA historical coverage"
    )


if __name__ == "__main__":
    main()
