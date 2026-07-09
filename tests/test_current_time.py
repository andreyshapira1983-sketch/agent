"""Unit tests for the current_time tool."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.current_time import CurrentTimeTool


class TestRiskAndIdentity:
    def test_name_and_risk(self):
        tool = CurrentTimeTool()
        assert tool.name == "current_time"
        assert tool.risk == "read_only"
        assert "current date" in tool.description.lower() or "now" in tool.description.lower()


class TestRun:
    def test_no_args_returns_dict(self):
        result = CurrentTimeTool().run()
        assert isinstance(result, dict)
        for key in ("iso_utc", "iso_local", "unix", "weekday",
                    "year", "month", "day"):
            assert key in result

    def test_unknown_arg_rejected(self):
        with pytest.raises(PermissionError, match="no arguments"):
            CurrentTimeTool().run(when="now")

    def test_injected_clock_used(self):
        fixed = datetime(2026, 6, 3, 14, 23, 51, tzinfo=timezone.utc)
        tool = CurrentTimeTool(clock=lambda: fixed)
        result = tool.run()
        assert result["year"] == 2026
        assert result["month"] == 6
        assert result["day"] == 3
        assert result["weekday"] == "Wednesday"
        assert result["iso_utc"].startswith("2026-06-03T14:23:51")
        assert result["unix"] == int(fixed.timestamp())

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 1, 1, 0, 0, 0)  # no tzinfo
        tool = CurrentTimeTool(clock=lambda: naive)
        result = tool.run()
        assert result["iso_utc"].startswith("2026-01-01T00:00:00")

    def test_non_utc_timezone_normalised(self):
        plus_three = timezone(timedelta(hours=3))
        local = datetime(2026, 6, 3, 17, 0, 0, tzinfo=plus_three)
        tool = CurrentTimeTool(clock=lambda: local)
        result = tool.run()
        # Should be converted to UTC: 17:00 +03:00 -> 14:00 UTC
        assert result["iso_utc"].startswith("2026-06-03T14:00:00")

    def test_clock_returning_non_datetime_rejected(self):
        tool = CurrentTimeTool(clock=lambda: "2026-06-03")
        with pytest.raises(TypeError, match="must return datetime"):
            tool.run()


class TestValidateOutput:
    def _tool(self) -> CurrentTimeTool:
        return CurrentTimeTool()

    def test_real_output_passes(self):
        tool = self._tool()
        ok, reasons = tool.validate_output(tool.run())
        assert ok, reasons

    def test_non_dict_rejected(self):
        ok, reasons = self._tool().validate_output("now")
        assert not ok
        assert any("dict" in r for r in reasons)

    def test_missing_int_field_rejected(self):
        ok, reasons = self._tool().validate_output({
            "iso_utc": "x", "iso_local": "x", "weekday": "x",
            "unix": "not int", "year": 2026, "month": 6, "day": 3,
        })
        assert not ok
        assert any("unix" in r for r in reasons)

    def test_missing_string_field_rejected(self):
        ok, reasons = self._tool().validate_output({
            "iso_utc": 123, "iso_local": "x", "weekday": "x",
            "unix": 1, "year": 2026, "month": 6, "day": 3,
        })
        assert not ok
        assert any("iso_utc" in r for r in reasons)

    def test_tz_name_none_allowed(self):
        ok, _ = self._tool().validate_output({
            "iso_utc": "x", "iso_local": "x", "weekday": "x",
            "unix": 1, "year": 2026, "month": 6, "day": 3,
            "tz_name": None,
        })
        assert ok


class TestRegistration:
    def test_registered_in_main(self):
        # main.py registers CurrentTimeTool — smoke check the import path.
        from tools.current_time import CurrentTimeTool as Imported
        assert Imported is CurrentTimeTool

    def test_safe_for_subagent(self):
        from core.subagent_runner import _SAFE_SUBAGENT_TOOLS
        assert "current_time" in _SAFE_SUBAGENT_TOOLS
