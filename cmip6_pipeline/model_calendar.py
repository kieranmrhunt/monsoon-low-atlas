"""Lossless native-calendar identity on a Gregorian analysis clock.

The frozen detector and linker use pandas timestamps.  For a CMIP6 run with a
non-Gregorian calendar, we therefore expose a strictly ordinal analysis clock
to those unchanged algorithms and retain an invertible mapping back to native
model time.  No model day is inserted, removed, stretched or relabelled as a
civil date.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import cftime
import numpy as np
import pandas as pd


SCHEMA = "lps-atlas-cmip6-time-axis-v1"
GREGORIAN_CALENDARS = {"standard", "gregorian", "proleptic_gregorian"}
SUPPORTED_CALENDARS = GREGORIAN_CALENDARS | {"360_day", "365_day", "noleap"}


def next_month(month: str) -> str:
    year, number = int(month[:4]), int(month[4:])
    if not (1 <= number <= 12):
        raise ValueError(f"invalid model month {month!r}")
    number += 1
    if number == 13:
        year, number = year + 1, 1
    return f"{year:04d}{number:02d}"


def previous_month(month: str) -> str:
    year, number = int(month[:4]), int(month[4:])
    if not (1 <= number <= 12):
        raise ValueError(f"invalid model month {month!r}")
    number -= 1
    if number == 0:
        year, number = year - 1, 12
    return f"{year:04d}{number:02d}"


def native_month_start(month: str, calendar: str) -> cftime.datetime:
    return cftime.datetime(int(month[:4]), int(month[4:]), 1, calendar=calendar)


def native_iso(value: Any) -> str:
    return (
        f"{int(value.year):04d}-{int(value.month):02d}-{int(value.day):02d}"
        f"T{int(value.hour):02d}:{int(value.minute):02d}:{int(value.second):02d}"
    )


def native_stamp(value: Any) -> str:
    return (
        f"{int(value.year):04d}{int(value.month):02d}{int(value.day):02d}"
        f"{int(value.hour):02d}{int(value.minute):02d}{int(value.second):02d}"
    )


@dataclass(frozen=True)
class TimeAxis:
    calendar: str
    native_anchor: str
    analysis_anchor: str
    schema: str = SCHEMA
    basis: str = "continuous_native_elapsed_hours_on_gregorian_analysis_clock"

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"unsupported CMIP6 time-axis schema {self.schema!r}")
        if self.calendar not in SUPPORTED_CALENDARS:
            raise ValueError(f"unsupported CMIP6 calendar {self.calendar!r}")
        pd.Timestamp(self.analysis_anchor)
        self._native_anchor_value()

    @property
    def is_identity(self) -> bool:
        return self.calendar in GREGORIAN_CALENDARS

    @property
    def units(self) -> str:
        return f"hours since {self.native_anchor.replace('T', ' ')}"

    def _native_anchor_value(self) -> cftime.datetime:
        stamp = self.native_anchor.replace("T", " ")
        date, clock = stamp.split(" ", maxsplit=1)
        year, month, day = (int(value) for value in date.split("-"))
        hour, minute, second = (int(value) for value in clock.split(":"))
        return cftime.datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            calendar=self.calendar,
        )

    def native_to_analysis(self, values: Sequence[Any] | Any) -> pd.DatetimeIndex:
        if np.ndim(values) == 0:
            source = [values]
        else:
            source = list(values)
        if self.is_identity:
            return pd.DatetimeIndex(
                [
                    pd.Timestamp(
                        year=int(value.year),
                        month=int(value.month),
                        day=int(value.day),
                        hour=int(value.hour),
                        minute=int(value.minute),
                        second=int(value.second),
                    )
                    if hasattr(value, "year")
                    else pd.Timestamp(value)
                    for value in source
                ]
            )
        offsets = np.asarray(
            cftime.date2num(source, self.units, calendar=self.calendar),
            dtype=float,
        )
        rounded = np.rint(offsets * 3_600_000_000_000.0).astype("timedelta64[ns]")
        return pd.DatetimeIndex(pd.Timestamp(self.analysis_anchor).to_datetime64() + rounded)

    def analysis_to_native(self, values: Iterable[Any] | Any) -> list[Any]:
        if isinstance(values, (str, pd.Timestamp, np.datetime64)):
            analysis = pd.DatetimeIndex([pd.Timestamp(values)])
        else:
            analysis = pd.DatetimeIndex(pd.to_datetime(list(values), errors="raise"))
        delta_hours = (
            (analysis - pd.Timestamp(self.analysis_anchor)).total_seconds().to_numpy()
            / 3600.0
        )
        converted = cftime.num2date(
            delta_hours,
            self.units,
            calendar=self.calendar,
            only_use_cftime_datetimes=True,
        )
        return list(np.atleast_1d(converted))

    def analysis_interval_for_native_months(
        self,
        start_month: str,
        end_month: str,
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        start_native = native_month_start(start_month, self.calendar)
        end_native = native_month_start(next_month(end_month), self.calendar)
        converted = self.native_to_analysis([start_native, end_native])
        return pd.Timestamp(converted[0]), pd.Timestamp(converted[1])

    def native_bounds_for_analysis_interval(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> tuple[Any, Any]:
        values = self.analysis_to_native([start, end])
        return values[0], values[1]

    def annotate(self, frame: pd.DataFrame, *, time_column: str = "time") -> pd.DataFrame:
        output = frame.copy()
        analysis = pd.DatetimeIndex(pd.to_datetime(output[time_column], errors="raise"))
        native = self.analysis_to_native(analysis)
        output[time_column] = analysis
        output["model_time"] = [native_iso(value) for value in native]
        output["model_year"] = np.asarray([value.year for value in native], dtype=np.int32)
        output["model_month"] = np.asarray([value.month for value in native], dtype=np.int8)
        output["model_day"] = np.asarray([value.day for value in native], dtype=np.int8)
        output["model_hour"] = np.asarray([value.hour for value in native], dtype=np.int8)
        output["model_minute"] = np.asarray([value.minute for value in native], dtype=np.int8)
        output["model_calendar"] = self.calendar
        output["time_basis"] = self.basis
        return output

    def record(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_record(cls, value: dict[str, Any]) -> "TimeAxis":
        return cls(
            calendar=str(value["calendar"]),
            native_anchor=str(value["native_anchor"]),
            analysis_anchor=str(value["analysis_anchor"]),
            schema=str(value.get("schema", SCHEMA)),
            basis=str(
                value.get(
                    "basis",
                    "continuous_native_elapsed_hours_on_gregorian_analysis_clock",
                )
            ),
        )


def time_axis(calendar: str, anchor_month: str) -> TimeAxis:
    anchor = f"{anchor_month[:4]}-{anchor_month[4:]}-01T00:00:00"
    return TimeAxis(calendar=calendar, native_anchor=anchor, analysis_anchor=anchor)


def load_time_axis(path: Path | None) -> TimeAxis | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return TimeAxis.from_record(value)
