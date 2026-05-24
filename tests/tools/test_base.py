"""tests/tools/test_base.py — ToolResult and ToolBase helpers"""

import pytest
from tools.base import ToolBase, ToolResult, ToolSpec


# ------------------------------------------------------------------
# Minimal concrete tool for testing ToolBase helpers
# ------------------------------------------------------------------

class _EchoTool(ToolBase):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="echo",
            description="Echoes the input",
            parameters={"text": "str"},
        )

    def execute(self, **params):
        text = params.get("text", "")
        if text == "FAIL":
            return self._fail("intentional failure", reason="test")
        return self._ok(output=text, char_count=len(text))


# ------------------------------------------------------------------
# ToolResult
# ------------------------------------------------------------------

class TestToolResult:
    def test_as_text_success(self):
        r = ToolResult(tool_name="echo", success=True, output="hello")
        assert r.as_text() == "[echo] OK: hello"

    def test_as_text_failure(self):
        r = ToolResult(tool_name="echo", success=False, output=None, error="oops")
        assert r.as_text() == "[echo] ERROR: oops"

    def test_to_dict(self):
        r = ToolResult(tool_name="echo", success=True, output=42, metadata={"x": 1})
        d = r.to_dict()
        assert d["tool_name"] == "echo"
        assert d["success"] is True
        assert d["output"] == 42
        assert d["metadata"]["x"] == 1

    def test_metadata_defaults_empty(self):
        r = ToolResult(tool_name="t", success=True, output=None)
        assert r.metadata == {}


# ------------------------------------------------------------------
# ToolBase helpers
# ------------------------------------------------------------------

class TestToolBaseHelpers:
    def setup_method(self):
        self.tool = _EchoTool()

    def test_ok_sets_success(self):
        result = self.tool.execute(text="hi")
        assert result.success is True
        assert result.output == "hi"
        assert result.tool_name == "echo"

    def test_ok_metadata(self):
        result = self.tool.execute(text="hello")
        assert result.metadata["char_count"] == 5

    def test_fail_sets_failure(self):
        result = self.tool.execute(text="FAIL")
        assert result.success is False
        assert result.error == "intentional failure"
        assert result.output is None
        assert result.tool_name == "echo"

    def test_fail_metadata(self):
        result = self.tool.execute(text="FAIL")
        assert result.metadata["reason"] == "test"

    def test_timed_returns_result_and_float(self):
        tool = _EchoTool()
        result, elapsed = tool._timed(lambda: 42)
        assert result == 42
        assert isinstance(elapsed, float)
        assert elapsed >= 0


# ------------------------------------------------------------------
# ToolSpec
# ------------------------------------------------------------------

class TestToolSpec:
    def test_requires_approval_defaults_false(self):
        s = ToolSpec(name="x", description="d", parameters={})
        assert s.requires_approval is False

    def test_is_destructive_defaults_false(self):
        s = ToolSpec(name="x", description="d", parameters={})
        assert s.is_destructive is False

    def test_requires_approval_set(self):
        s = ToolSpec(name="x", description="d", parameters={}, requires_approval=True)
        assert s.requires_approval is True
