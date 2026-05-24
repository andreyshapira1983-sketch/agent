"""tests/tools/test_datetime_tool.py — DateTimeTool"""

import pytest
from datetime import datetime
from tools.builtins.datetime_tool import DateTimeTool


class TestDateTimeTool:
    def setup_method(self):
        self.tool = DateTimeTool()

    # spec
    def test_spec_name(self):
        assert self.tool.spec.name == "datetime"

    # now
    def test_now_success(self):
        r = self.tool.execute(action="now")
        assert r.success is True
        # Should be a valid ISO datetime
        dt = datetime.fromisoformat(r.output.replace("Z", "+00:00"))
        assert dt.year >= 2025

    def test_now_has_weekday(self):
        r = self.tool.execute(action="now")
        assert "weekday" in r.metadata

    def test_default_action_is_now(self):
        r = self.tool.execute()
        assert r.success is True  # action defaults to "now"

    # format
    def test_format_date(self):
        r = self.tool.execute(action="format", date="2026-05-15", fmt="%d/%m/%Y")
        assert r.success is True
        assert r.output == "15/05/2026"

    def test_format_default_fmt(self):
        r = self.tool.execute(action="format", date="2026-05-15")
        assert r.success is True
        assert r.output == "2026-05-15"

    def test_format_missing_date(self):
        r = self.tool.execute(action="format")
        assert r.success is False

    def test_format_invalid_date(self):
        r = self.tool.execute(action="format", date="not-a-date")
        assert r.success is False

    # diff_days
    def test_diff_days_positive(self):
        r = self.tool.execute(action="diff_days", date_from="2026-01-01", date_to="2026-01-11")
        assert r.success is True
        assert r.output == 10

    def test_diff_days_negative(self):
        r = self.tool.execute(action="diff_days", date_from="2026-01-11", date_to="2026-01-01")
        assert r.output == -10

    def test_diff_days_same_date(self):
        r = self.tool.execute(action="diff_days", date_from="2026-05-15", date_to="2026-05-15")
        assert r.output == 0

    def test_diff_days_missing_params(self):
        r = self.tool.execute(action="diff_days", date_from="2026-01-01")
        assert r.success is False

    # add_days
    def test_add_days_positive(self):
        r = self.tool.execute(action="add_days", date="2026-05-15", days=10)
        assert r.success is True
        assert r.output == "2026-05-25"

    def test_add_days_negative(self):
        r = self.tool.execute(action="add_days", date="2026-05-15", days=-15)
        assert r.success is True
        assert r.output == "2026-04-30"

    def test_add_days_zero(self):
        r = self.tool.execute(action="add_days", date="2026-05-15", days=0)
        assert r.output == "2026-05-15"

    def test_add_days_missing_date(self):
        r = self.tool.execute(action="add_days", days=5)
        assert r.success is False

    # unknown action
    def test_unknown_action(self):
        r = self.tool.execute(action="fly_to_moon")
        assert r.success is False
        assert "Unknown action" in r.error
