"""
tools/builtins/datetime_tool.py — Current date/time and date arithmetic

Brain uses this when it needs to know the current time,
format a date, or compute a time delta.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_SUPPORTED_ACTIONS = {"now", "format", "diff_days", "add_days"}


class DateTimeTool(ToolBase):
    """
    Date/time operations.

    actions:
        now         → current UTC datetime (ISO-8601)
        format      → reformat a date string (requires: date, fmt)
        diff_days   → days between two dates (requires: date_from, date_to)
        add_days    → add/subtract days from a date (requires: date, days)

    params:
        action    (str): One of the supported actions above.
        date      (str, optional): ISO-8601 date string, e.g. "2026-05-15"
        date_from (str, optional): Start date for diff_days.
        date_to   (str, optional): End date for diff_days.
        fmt       (str, optional): strftime format string (default "%Y-%m-%d").
        days      (int, optional): Number of days for add_days.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="datetime",
            description=(
                "Date/time operations: get current UTC time, format dates, "
                "compute day differences, add/subtract days"
            ),
            parameters={
                "action":    "str — 'now' | 'format' | 'diff_days' | 'add_days'",
                "date":      "str (optional) — ISO-8601 date, e.g. '2026-05-15'",
                "date_from": "str (optional) — start date for diff_days",
                "date_to":   "str (optional) — end date for diff_days",
                "fmt":       "str (optional) — strftime format, default '%Y-%m-%d'",
                "days":      "int (optional) — days to add for add_days",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        action: str = params.get("action", "now")
        if action not in _SUPPORTED_ACTIONS:
            return self._fail(
                f"Unknown action '{action}'. Supported: {sorted(_SUPPORTED_ACTIONS)}"
            )

        try:
            if action == "now":
                return self._action_now()
            if action == "format":
                return self._action_format(params)
            if action == "diff_days":
                return self._action_diff_days(params)
            if action == "add_days":
                return self._action_add_days(params)
        except (ValueError, TypeError) as exc:
            return self._fail(str(exc))

        return self._fail("Unhandled action")  # pragma: no cover

    # ------------------------------------------------------------------

    def _action_now(self) -> ToolResult:
        now = datetime.now(tz=timezone.utc)
        return self._ok(output=now.isoformat(), weekday=now.strftime("%A"))

    def _action_format(self, params: dict) -> ToolResult:
        date_str: str = params.get("date", "")
        fmt: str = params.get("fmt", "%Y-%m-%d")
        if not date_str:
            return self._fail("'date' param is required for action 'format'")
        dt = self._parse_date(date_str)
        return self._ok(output=dt.strftime(fmt))

    def _action_diff_days(self, params: dict) -> ToolResult:
        from_str: str = params.get("date_from", "")
        to_str: str = params.get("date_to", "")
        if not from_str or not to_str:
            return self._fail(
                "'date_from' and 'date_to' are required for action 'diff_days'"
            )
        d_from = self._parse_date(from_str)
        d_to = self._parse_date(to_str)
        delta = (d_to - d_from).days
        return self._ok(output=delta, date_from=from_str, date_to=to_str)

    def _action_add_days(self, params: dict) -> ToolResult:
        date_str: str = params.get("date", "")
        days: int = int(params.get("days", 0))
        if not date_str:
            return self._fail("'date' param is required for action 'add_days'")
        dt = self._parse_date(date_str)
        result = dt + timedelta(days=days)
        return self._ok(output=result.strftime("%Y-%m-%d"), days_added=days)

    @staticmethod
    def _parse_date(s: str) -> datetime:
        """Parse ISO-8601 date or datetime string."""
        s = s.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: '{s}'. Expected ISO-8601 (YYYY-MM-DD)")
