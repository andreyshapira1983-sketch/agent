"""tests/tools/test_executor.py — ToolExecutor"""

import time
import threading
import pytest
from tools.base import ToolBase, ToolResult, ToolSpec
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


# ------------------------------------------------------------------
# Test tools
# ------------------------------------------------------------------

def _make_registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


class OkTool(ToolBase):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="ok_tool", description="always succeeds", parameters={"x": "str"})

    def execute(self, **params) -> ToolResult:
        return self._ok(output=params.get("x", "default"))


class FailTool(ToolBase):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="fail_tool", description="always fails", parameters={})

    def execute(self, **params) -> ToolResult:
        return self._fail("always fails")


class ExplodeTool(ToolBase):
    """Raises exception inside execute — executor must catch it."""
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="explode_tool", description="raises", parameters={})

    def execute(self, **params) -> ToolResult:
        raise RuntimeError("BOOM")


class SlowTool(ToolBase):
    """Sleeps longer than timeout — for timeout test."""
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="slow_tool", description="sleeps", parameters={})

    def execute(self, **params) -> ToolResult:
        time.sleep(10)  # Will be killed by timeout
        return self._ok(output="done")


class DestructiveTool(ToolBase):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="destruct_tool",
            description="destructive",
            parameters={},
            is_destructive=True,
        )

    def execute(self, **params) -> ToolResult:
        return self._ok(output="deleted")


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestToolExecutor:
    def setup_method(self):
        self.reg = _make_registry(
            OkTool(), FailTool(), ExplodeTool(), SlowTool(), DestructiveTool()
        )
        self.ex = ToolExecutor(self.reg, default_timeout=2.0)

    def test_ok_tool_success(self):
        result = self.ex.run("ok_tool", params={"x": "hello"})
        assert result.success is True
        assert result.output == "hello"
        assert result.tool_name == "ok_tool"

    def test_ok_tool_no_params(self):
        result = self.ex.run("ok_tool")
        assert result.success is True
        assert result.output == "default"

    def test_fail_tool_returns_failure(self):
        result = self.ex.run("fail_tool")
        assert result.success is False
        assert "always fails" in result.error

    def test_unknown_tool_returns_failure(self):
        result = self.ex.run("ghost_tool")
        assert result.success is False
        assert "ghost_tool" in result.error

    def test_explode_tool_is_isolated(self):
        result = self.ex.run("explode_tool")
        assert result.success is False
        assert "BOOM" in result.error or "Unexpected error" in result.error

    def test_slow_tool_timeout(self):
        result = self.ex.run("slow_tool", timeout=0.2)
        assert result.success is False
        assert "timed out" in result.error.lower()
        assert result.metadata.get("timed_out") is True

    def test_destructive_blocked_without_flag(self):
        result = self.ex.run("destruct_tool")  # allow_destructive defaults False
        assert result.success is False
        assert "destructive" in result.error.lower()

    def test_destructive_allowed_with_flag(self):
        result = self.ex.run("destruct_tool", allow_destructive=True)
        assert result.success is True
        assert result.output == "deleted"

    def test_params_none_defaults_empty(self):
        result = self.ex.run("ok_tool", params=None)
        assert result.success is True

    def test_tool_name_in_result(self):
        result = self.ex.run("fail_tool")
        assert result.tool_name == "fail_tool"
