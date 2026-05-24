"""tests/tools/test_memory_search.py — MemorySearchTool"""

import pytest
from tools.builtins.memory_search import MemorySearchTool


# ------------------------------------------------------------------
# Mock memory
# ------------------------------------------------------------------

class MockMemory:
    def __init__(self):
        self._data: dict[str, list] = {}

    def store(self, session_id: str, role: str, content: str) -> None:
        self._data.setdefault(session_id, []).append((role, content))

    def retrieve(self, session_id: str) -> list:
        return self._data.get(session_id, [])

    def inject(self, session_id: str, entries: list[tuple]) -> None:
        self._data[session_id] = list(entries)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestMemorySearchTool:
    def setup_method(self):
        self.memory = MockMemory()
        self.tool = MemorySearchTool(memory=self.memory)

    def test_spec_name(self):
        assert self.tool.spec.name == "memory_search"

    def test_spec_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_finds_matching_entries(self):
        self.memory.inject("s1", [
            ("user", "Python is great"),
            ("assistant", "I agree!"),
            ("user", "Tell me about Java"),
        ])
        r = self.tool.execute(session_id="s1", query="Python")
        assert r.success is True
        assert len(r.output) == 1
        assert "Python" in str(r.output[0])

    def test_case_insensitive_search(self):
        self.memory.inject("s1", [("user", "PYTHON rocks")])
        r = self.tool.execute(session_id="s1", query="python")
        assert r.success is True
        assert len(r.output) == 1

    def test_no_matches_returns_empty(self):
        self.memory.inject("s1", [("user", "cats are cute")])
        r = self.tool.execute(session_id="s1", query="Python")
        assert r.success is True
        assert r.output == []

    def test_empty_session_returns_empty(self):
        r = self.tool.execute(session_id="ghost", query="anything")
        assert r.success is True
        assert r.output == []

    def test_missing_session_id(self):
        r = self.tool.execute(query="Python")
        assert r.success is False
        assert "session_id" in r.error

    def test_missing_query(self):
        r = self.tool.execute(session_id="s1")
        assert r.success is False
        assert "query" in r.error

    def test_limit_respected(self):
        self.memory.inject("s1", [
            ("user", "Python 1"),
            ("user", "Python 2"),
            ("user", "Python 3"),
            ("user", "Python 4"),
            ("user", "Python 5"),
        ])
        r = self.tool.execute(session_id="s1", query="Python", limit=2)
        assert r.success is True
        assert len(r.output) == 2

    def test_limit_too_low(self):
        r = self.tool.execute(session_id="s1", query="q", limit=0)
        assert r.success is False

    def test_limit_too_high(self):
        r = self.tool.execute(session_id="s1", query="q", limit=200)
        assert r.success is False

    def test_metadata_has_query(self):
        self.memory.inject("s1", [("user", "test")])
        r = self.tool.execute(session_id="s1", query="test")
        assert r.metadata["query"] == "test"
        assert r.metadata["session_id"] == "s1"
