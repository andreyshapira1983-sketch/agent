"""tests/tools/test_registry.py — ToolRegistry"""

import pytest
from tools.base import ToolBase, ToolResult, ToolSpec
from tools.registry import ToolNotFoundError, ToolRegistry


# ------------------------------------------------------------------
# Minimal tools
# ------------------------------------------------------------------

def _make_tool(name: str, requires_approval=False, is_destructive=False) -> ToolBase:
    class _T(ToolBase):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=name,
                description=f"tool {name}",
                parameters={"x": "str"},
                requires_approval=requires_approval,
                is_destructive=is_destructive,
            )

        def execute(self, **params) -> ToolResult:
            return self._ok(output=params.get("x"))

    return _T()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestToolRegistry:
    def setup_method(self):
        self.reg = ToolRegistry()

    def test_register_and_get(self):
        self.reg.register(_make_tool("calc"))
        tool = self.reg.get("calc")
        assert tool.spec.name == "calc"

    def test_register_returns_self(self):
        result = self.reg.register(_make_tool("a"))
        assert result is self.reg

    def test_chainable_registration(self):
        self.reg.register(_make_tool("a")).register(_make_tool("b"))
        assert self.reg.has("a")
        assert self.reg.has("b")

    def test_register_many(self):
        self.reg.register_many(_make_tool("x"), _make_tool("y"), _make_tool("z"))
        assert len(self.reg) == 3

    def test_duplicate_raises(self):
        self.reg.register(_make_tool("dup"))
        with pytest.raises(ValueError, match="already registered"):
            self.reg.register(_make_tool("dup"))

    def test_get_missing_raises_tool_not_found(self):
        with pytest.raises(ToolNotFoundError):
            self.reg.get("missing")

    def test_has_true_false(self):
        self.reg.register(_make_tool("t1"))
        assert self.reg.has("t1") is True
        assert self.reg.has("ghost") is False

    def test_names_sorted(self):
        self.reg.register_many(_make_tool("z"), _make_tool("a"), _make_tool("m"))
        assert self.reg.names() == ["a", "m", "z"]

    def test_len(self):
        assert len(self.reg) == 0
        self.reg.register(_make_tool("t"))
        assert len(self.reg) == 1

    def test_requires_approval_true(self):
        self.reg.register(_make_tool("approved", requires_approval=True))
        assert self.reg.requires_approval("approved") is True

    def test_requires_approval_false(self):
        self.reg.register(_make_tool("safe"))
        assert self.reg.requires_approval("safe") is False

    def test_is_destructive(self):
        self.reg.register(_make_tool("danger", is_destructive=True))
        assert self.reg.is_destructive("danger") is True

    def test_specs_returns_list(self):
        self.reg.register_many(_make_tool("a"), _make_tool("b"))
        specs = self.reg.specs()
        assert len(specs) == 2
        names = {s.name for s in specs}
        assert names == {"a", "b"}

    def test_specs_as_text_contains_names(self):
        self.reg.register_many(_make_tool("web_search"), _make_tool("calculator"))
        text = self.reg.specs_as_text()
        assert "web_search" in text
        assert "calculator" in text

    def test_specs_as_text_approval_flag(self):
        self.reg.register(_make_tool("risky", requires_approval=True))
        text = self.reg.specs_as_text()
        assert "requires_approval" in text

    def test_specs_as_text_destructive_flag(self):
        self.reg.register(_make_tool("boom", is_destructive=True))
        text = self.reg.specs_as_text()
        assert "destructive" in text

    def test_repr(self):
        self.reg.register(_make_tool("t"))
        assert "t" in repr(self.reg)
