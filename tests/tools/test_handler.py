"""tests/tools/test_handler.py — ToolHandler"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tools.base import ToolBase, ToolResult, ToolSpec
from tools.executor import ToolExecutor
from tools.handler import ToolHandler, _parse_content
from tools.registry import ToolRegistry
from brain.core import ThinkResult


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_ok_tool(name="calc"):
    class _T(ToolBase):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(name=name, description="test", parameters={})
        def execute(self, **params) -> ToolResult:
            return self._ok(output="result_ok")
    return _T()


def _make_fail_tool(name="fail"):
    class _T(ToolBase):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(name=name, description="test", parameters={})
        def execute(self, **params) -> ToolResult:
            return self._fail("tool_failed")
    return _T()


def _make_destructive_tool(name="danger"):
    class _T(ToolBase):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(name=name, description="test", parameters={}, is_destructive=True)
        def execute(self, **params) -> ToolResult:
            return self._ok(output="destroyed")
    return _T()


def _make_loop_mock():
    loop = MagicMock()
    loop.submit = AsyncMock(return_value=True)
    return loop


def _make_think_result(action="tool_call", content=None):
    return ThinkResult(
        action=action,
        content=content or {"tool_name": "calc", "params": {}},
        confidence=0.9,
        reasoning="test",
    )


# ------------------------------------------------------------------
# _parse_content
# ------------------------------------------------------------------

class TestParseContent:
    def test_valid(self):
        name, params, err = _parse_content({"tool_name": "calc", "params": {"x": 1}})
        assert name == "calc"
        assert params == {"x": 1}
        assert err is None

    def test_tool_alias(self):
        name, params, err = _parse_content({"tool": "calc"})
        assert name == "calc"
        assert err is None

    def test_name_alias(self):
        name, params, err = _parse_content({"name": "calc"})
        assert name == "calc"
        assert err is None

    def test_missing_tool_name(self):
        _, _, err = _parse_content({"params": {}})
        assert err is not None
        assert "tool_name" in err

    def test_not_dict(self):
        _, _, err = _parse_content("just a string")
        assert err is not None

    def test_params_default_empty(self):
        _, params, err = _parse_content({"tool_name": "calc"})
        assert params == {}
        assert err is None

    def test_params_alias_parameters(self):
        _, params, err = _parse_content({"tool_name": "calc", "parameters": {"k": "v"}})
        assert params == {"k": "v"}

    def test_params_alias_args(self):
        _, params, err = _parse_content({"tool_name": "calc", "args": {"k": "v"}})
        assert params == {"k": "v"}

    def test_params_not_dict(self):
        _, _, err = _parse_content({"tool_name": "calc", "params": "bad"})
        assert err is not None


# ------------------------------------------------------------------
# ToolHandler.handle
# ------------------------------------------------------------------

class TestToolHandler:
    def _make_handler(self, *tools, fail_tools=()):
        reg = ToolRegistry()
        for t in tools:
            reg.register(t)
        for t in fail_tools:
            reg.register(t)
        ex = ToolExecutor(reg)
        loop = _make_loop_mock()
        handler = ToolHandler(reg, ex, loop)
        return handler, loop

    async def test_successful_tool_call(self):
        handler, loop = self._make_handler(_make_ok_tool("calc"))
        result = _make_think_result(content={"tool_name": "calc", "params": {}})
        await handler.handle("sess1", result.content, result)
        loop.submit.assert_called_once()
        call_args = loop.submit.call_args[0][0]
        assert call_args.source == "tool_result"
        assert "calc" in call_args.content
        assert "succeeded" in call_args.content

    async def test_failed_tool_call(self):
        handler, loop = self._make_handler(_make_fail_tool("fail"))
        result = _make_think_result(content={"tool_name": "fail", "params": {}})
        await handler.handle("sess1", result.content, result)
        call_args = loop.submit.call_args[0][0]
        assert "failed" in call_args.content

    async def test_unknown_tool(self):
        handler, loop = self._make_handler(_make_ok_tool("calc"))
        result = _make_think_result(content={"tool_name": "ghost", "params": {}})
        await handler.handle("sess1", result.content, result)
        call_args = loop.submit.call_args[0][0]
        assert "failed" in call_args.content

    async def test_parse_error_feeds_back(self):
        handler, loop = self._make_handler(_make_ok_tool())
        await handler.handle("sess1", "not a dict", _make_think_result())
        loop.submit.assert_called_once()
        call_args = loop.submit.call_args[0][0]
        assert "failed" in call_args.content

    async def test_metadata_in_feedback(self):
        handler, loop = self._make_handler(_make_ok_tool("calc"))
        result = _make_think_result(content={"tool_name": "calc", "params": {}})
        await handler.handle("sess1", result.content, result)
        meta = loop.submit.call_args[0][0].metadata
        assert meta["tool_name"] == "calc"
        assert meta["success"] is True

    async def test_destructive_tool_allowed(self):
        handler, loop = self._make_handler(_make_destructive_tool("danger"))
        result = _make_think_result(content={"tool_name": "danger", "params": {}})
        await handler.handle("sess1", result.content, result)
        call_args = loop.submit.call_args[0][0]
        assert "succeeded" in call_args.content

    async def test_session_id_in_feedback_message(self):
        handler, loop = self._make_handler(_make_ok_tool("calc"))
        result = _make_think_result(content={"tool_name": "calc", "params": {}})
        await handler.handle("alice-session", result.content, result)
        msg = loop.submit.call_args[0][0]
        assert msg.session_id == "alice-session"
