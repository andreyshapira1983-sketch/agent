"""Current Time tool — pure read-only clock query.

Motivation: the agent has no implicit awareness of the current date.
Without this tool, asking "when was this README written?" leads the
LLM to guess from training-cutoff or copy a hard-coded date. A tiny
deterministic clock primitive removes that whole class of hallucination.

Returns a structured dict:
    {
        "iso_utc":   "2026-06-03T14:23:51+00:00",
        "iso_local": "2026-06-03T17:23:51+03:00",
        "unix":      1780000000,
        "tz_name":   "Europe/Moscow",   # best-effort, may be None
        "weekday":   "Wednesday",
        "year":      2026,
        "month":     6,
        "day":       3,
    }

No arguments, no side effects, no network. Always `read_only` risk.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from tools.base import Tool

# TODO: add format_duration_seconds(seconds: int|float) -> str as "Xh Ym Zs" / "Ym Zs" / "Zs"; negatives raise ValueError


class CurrentTimeTool(Tool):
    name = "current_time"
    description = (
        "Return the current date and time. Use this whenever the answer "
        "depends on 'now' (today's date, age of a document, freshness of "
        "a fact). Returns a dict with iso_utc, iso_local, unix epoch, "
        "weekday, and calendar fields. No arguments. No side effects."
    )
    risk = "read_only"

    def __init__(self, *, clock: Any = None):
        # `clock` is an injection seam for tests — must be a callable
        # returning a `datetime`. Production uses real wall-clock time.
        self._clock = clock

    def _now_utc(self) -> datetime:
        if self._clock is not None:
            value = self._clock()
            if not isinstance(value, datetime):
                raise TypeError(
                    f"current_time clock must return datetime, "
                    f"got {type(value).__name__}"
                )
            if value.tzinfo is None:
                # Treat naive datetime as UTC — never assume local.
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    def run(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            raise PermissionError(
                f"current_time accepts no arguments, got {sorted(kwargs)}"
            )

        utc = self._now_utc()
        local = utc.astimezone()  # uses system local tz

        tz_name: str | None = None
        try:
            tz_name = time.tzname[time.daylight] if time.daylight else time.tzname[0]
        except (IndexError, AttributeError):
            tz_name = None

        return {
            "iso_utc": utc.isoformat(),
            "iso_local": local.isoformat(),
            "unix": int(utc.timestamp()),
            "tz_name": tz_name,
            "weekday": utc.strftime("%A"),
            "year": utc.year,
            "month": utc.month,
            "day": utc.day,
        }

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not isinstance(output, dict):
            return False, [f"expected dict, got {type(output).__name__}"]
        for key in ("iso_utc", "iso_local", "weekday"):
            if not isinstance(output.get(key), str):
                reasons.append(f"{key} must be a string")
        for key in ("unix", "year", "month", "day"):
            if not isinstance(output.get(key), int):
                reasons.append(f"{key} must be an int")
        if "tz_name" in output and output["tz_name"] is not None and not isinstance(
            output["tz_name"], str
        ):
            reasons.append("tz_name must be a string or None")
        return (not reasons), reasons
