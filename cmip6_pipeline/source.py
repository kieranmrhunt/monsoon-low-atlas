"""Resolve high-frequency CMIP6 fields from the BADC DRS tree."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


DEFAULT_ROOT = Path("/badc/cmip6/data/CMIP6")
PERIOD_RE = re.compile(r"_(\d{4,14})-(\d{4,14})\.nc$")


@dataclass(frozen=True)
class RunSpec:
    activity: str
    institution: str
    source_id: str
    experiment_id: str
    member_id: str
    grid_label: str = "gn"

    @property
    def slug(self) -> str:
        return "_".join(
            (
                self.source_id,
                self.experiment_id,
                self.member_id,
                self.grid_label,
            )
        ).replace("/", "-")

    def field_directory(self, root: Path, table_id: str, variable_id: str) -> Path:
        base = (
            root
            / self.activity
            / self.institution
            / self.source_id
            / self.experiment_id
            / self.member_id
            / table_id
            / variable_id
            / self.grid_label
        )
        latest = base / "latest"
        if latest.is_dir():
            return latest
        versions = sorted(path for path in base.glob("v*") if path.is_dir())
        if not versions:
            raise FileNotFoundError(f"no CMIP6 versions below {base}")
        return versions[-1]


def _stamp(value: str, *, end: bool) -> pd.Timestamp:
    padded = value.ljust(14, "9" if end else "0")
    return pd.Timestamp(
        year=int(padded[0:4]),
        month=int(padded[4:6]),
        day=min(int(padded[6:8]), 28) if len(value) < 8 else int(padded[6:8]),
        hour=min(int(padded[8:10]), 23),
        minute=min(int(padded[10:12]), 59),
        second=min(int(padded[12:14]), 59),
    )


def period(path: Path) -> tuple[pd.Timestamp, pd.Timestamp]:
    match = PERIOD_RE.search(path.name)
    if match is None:
        raise ValueError(f"cannot read CMIP6 period from {path.name}")
    return _stamp(match.group(1), end=False), _stamp(match.group(2), end=True)


def period_stamps(path: Path) -> tuple[str, str]:
    """Return sortable 14-digit DRS bounds without assuming a civil calendar."""

    match = PERIOD_RE.search(path.name)
    if match is None:
        raise ValueError(f"cannot read CMIP6 period from {path.name}")
    return match.group(1).ljust(14, "0"), match.group(2).ljust(14, "9")


def files_overlapping(directory: Path, start: pd.Timestamp, end: pd.Timestamp) -> list[Path]:
    """Return CMIP6 segments intersecting [start, end], including its halo."""

    selected: list[Path] = []
    for path in sorted(directory.glob("*.nc")):
        try:
            first, last = period(path)
        except ValueError:
            continue
        if last >= start and first <= end:
            selected.append(path)
    if not selected:
        raise FileNotFoundError(f"no files in {directory} overlap {start}..{end}")
    return selected


def files_overlapping_stamps(directory: Path, start: str, end: str) -> list[Path]:
    """Return segments intersecting compact native-calendar bounds.

    CMIP6 DRS timestamps are lexically ordered within a calendar. Comparing
    their zero/nine-padded forms avoids constructing invalid civil dates such
    as 30 February in a 360-day integration.
    """

    lower = start.ljust(14, "0")
    upper = end.ljust(14, "9")
    selected: list[Path] = []
    for path in sorted(directory.glob("*.nc")):
        try:
            first, last = period_stamps(path)
        except ValueError:
            continue
        if last >= lower and first <= upper:
            selected.append(path)
    if not selected:
        raise FileNotFoundError(f"no files in {directory} overlap native time {lower}..{upper}")
    return selected
