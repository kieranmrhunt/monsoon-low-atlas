#!/usr/bin/env python3
"""Probe one ECDS TIGGE field family for a model cycle."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from .forecast_core import parse_cycle
from .sources import TIGGE_CENTRES, TiggeAdapter, available_forecast_steps


COMPONENTS = {
    "pressure": ("pl", ("131", "132")),
    "surface": ("sfc", ("151", "165", "166")),
    "precipitation": ("sfc", ("228",)),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=sorted(TIGGE_CENTRES), required=True)
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--component", choices=sorted(COMPONENTS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cycle = parse_cycle(args.cycle)
    steps = available_forecast_steps(args.model, cycle)
    level_type, parameters = COMPONENTS[args.component]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.part")
    temporary.unlink(missing_ok=True)
    try:
        TiggeAdapter(args.model)._retrieve(
            cycle,
            steps,
            temporary,
            TIGGE_CENTRES[args.model].forecast_types,
            level_type,
            parameters,
        )
        hasher = hashlib.sha256()
        with temporary.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        f"TIGGE probe passed: {args.model} {args.cycle} {args.component}; "
        f"{args.output.stat().st_size} bytes; sha256={digest}"
    )


if __name__ == "__main__":
    main()
