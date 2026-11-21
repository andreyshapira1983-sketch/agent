"""Base tool abstractions — Tool.invoke + ToolRegistry + default validate_output.

`tools/base.py` is the foundation every tool inherits from. A bug here would
ripple through every step of every plan, so it needs explicit coverage even
though concrete tools (`file_read`, `web_search`) already exercise it indirectly.
"""
from __future__ import annotations

from typing import Any

import pytest

from tools.base import Tool, ToolRegistry

# ============================================================
# Fixtures: minimal Tool subclasses for unit testing
# ============================================================


class _OkTool(Tool):
    name = "ok"
    description = "always-ok stub"
    risk = "read_only"

    def __init__(self, output: Any = "fine"):
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.output


# ============================================================
# Default validate_output (§5)
# ============================================================

class TestDefaultValidateOutput:
    def test_none_is_hard_fail(self):
        ok, issues = _OkTool().validate_output(None)
        assert ok is False
        assert any("None" in i for i in issues)

    @pytest.mark.parametrize("empty", ["", [], {}])
    def test_empty_string_list_dict_are_hard_fail(self, empty):
        ok, issues = _OkTool().validate_output(empty)
        assert ok is False
        assert any("empty" in i for i in issues)


# ============================================================
# ToolRegistry — registration, lookup, listing, describe
# ============================================================

class TestToolRegistry:
    def test_register_same_name_twice_rejected(self):
        reg = ToolRegistry()
        reg.register(_OkTool())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_OkTool())


    def test_describe_includes_name_risk_description(self):
        reg = ToolRegistry()
        reg.register(_OkTool())
        text = reg.describe()
        assert "ok" in text
        assert "read_only" in text
        assert "always-ok stub" in text

    def test_empty_registry_describe(self):
        # No tools yet: describe must not crash; an empty string is fine.
        assert ToolRegistry().describe() == ""
