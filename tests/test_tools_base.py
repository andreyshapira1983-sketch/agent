"""Base tool abstractions — Tool.invoke + ToolRegistry + default validate_output.

`tools/base.py` is the foundation every tool inherits from. A bug here would
ripple through every step of every plan, so it needs explicit coverage even
though concrete tools (`file_read`, `web_search`) already exercise it indirectly.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.models import ToolCall
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


class _RaisingTool(Tool):
    name = "boom"
    description = "raises whatever you tell it to"
    risk = "read_only"

    def __init__(self, exc: BaseException):
        self.exc = exc

    def run(self, **kwargs: Any) -> Any:
        raise self.exc


# ============================================================
# Tool.invoke — happy path + every error shape
# ============================================================

class TestToolInvoke:
    def test_success_wraps_output_in_tool_result(self):
        tool = _OkTool(output="hi")
        call = ToolCall(action_id="act_x", tool_name="ok", arguments={"a": 1})
        result = tool.invoke(call)

        assert result.status == "success"
        assert result.output == "hi"
        assert result.error is None
        assert result.tool_call_id == call.id
        # Latency is measured; just sanity-check it's a non-negative int.
        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0
        # Arguments were forwarded.
        assert tool.calls == [{"a": 1}]

    def test_exception_becomes_error_status(self):
        tool = _RaisingTool(ValueError("nope"))
        call = ToolCall(action_id="act_y", tool_name="boom", arguments={})
        result = tool.invoke(call)

        assert result.status == "error"
        assert result.output is None
        # Error message preserves both type and message — important for
        # the loop's `tool_error` ReplanTrigger reason field.
        assert "ValueError" in (result.error or "")
        assert "nope" in (result.error or "")
        assert result.tool_call_id == call.id

    def test_keyboard_interrupt_is_NOT_swallowed_silently(self):
        # KeyboardInterrupt inherits from BaseException; Python's except-Exception
        # ignores it. We rely on that behaviour: Ctrl-C escapes the tool, not
        # gets captured as a "tool error" result.
        tool = _RaisingTool(KeyboardInterrupt())
        call = ToolCall(action_id="act_z", tool_name="boom", arguments={})
        with pytest.raises(KeyboardInterrupt):
            tool.invoke(call)

    def test_invoke_preserves_idempotency_key(self):
        tool = _OkTool()
        call = ToolCall(action_id="act_q", tool_name="ok", arguments={})
        result = tool.invoke(call)
        assert result.tool_call_id == call.id
        # idempotency_key on the call is preserved on the call object;
        # ensure invoke didn't mutate the input.
        assert call.idempotency_key.startswith("idem_")


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

    @pytest.mark.parametrize(
        "value",
        ["hello", [1, 2], {"a": 1}, 0, False, True, 3.14],
    )
    def test_non_empty_or_scalar_passes(self, value):
        ok, issues = _OkTool().validate_output(value)
        assert ok is True
        assert issues == []


# ============================================================
# ToolRegistry — registration, lookup, listing, describe
# ============================================================

class TestToolRegistry:
    def test_get_unknown_raises_keyerror(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="ghost"):
            reg.get("ghost")

    def test_register_then_get_round_trip(self):
        reg = ToolRegistry()
        t = _OkTool()
        reg.register(t)
        assert reg.get("ok") is t

    def test_register_same_name_twice_rejected(self):
        reg = ToolRegistry()
        reg.register(_OkTool())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_OkTool())

    def test_list_returns_every_registered_tool(self):
        reg = ToolRegistry()
        a = _OkTool()

        class _OtherTool(Tool):
            name = "other"
            description = "x"
            risk = "read_only"

            def run(self, **kwargs):
                return "x"

        b = _OtherTool()
        reg.register(a)
        reg.register(b)

        names = {t.name for t in reg.list()}
        assert names == {"ok", "other"}

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

    def test_empty_registry_list_is_empty_list(self):
        assert ToolRegistry().list() == []
