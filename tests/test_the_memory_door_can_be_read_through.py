"""The memory door can be read through — bounded, low-trust, by his own hand.

Authority change 2026-09-04: the write door (`memory_bank`) opened in the
morning; without an explicit read the exam STORE → RETRIEVE → USE cannot be
sat, because passive `<long_term_memory>` injection is the loop's choice,
not his. Walls pinned here: at most 5 records, active store only, newest
first, every record carries LOW-TRUST and its provenance, an unreadable
store is «unavailable» (never «empty memory»), and the tool is registered
for the unattended path with a reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tools.memory_recall import MAX_RECORDS, MemoryRecallTool


def _rec(i: int, content: str, *, days_ago: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"mem_{i}", type="semantic", content=content, source="agent-auto",
        created_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc) - timedelta(days=days_ago),
    )


class _Store:
    def __init__(self, records, *, raises=False):
        self._records, self._raises = records, raises

    def load(self):
        if self._raises:
            raise OSError("state file locked")
        return list(self._records)


def test_at_most_five_newest_first_each_marked_low_trust_with_provenance():
    records = [_rec(i, f"convergence note {i}", days_ago=i) for i in range(8)]
    tool = MemoryRecallTool(store=_Store(records))

    out = tool.run(term="CONVERGENCE")

    assert out["count"] == MAX_RECORDS == 5 and out["more"] == 3
    assert [r["id"] for r in out["records"]] == ["mem_0", "mem_1", "mem_2", "mem_3", "mem_4"]
    for r in out["records"]:
        assert r["trust"].startswith("LOW-TRUST") and r["source"] == "agent-auto"
    assert out["trust"] == "low"


def test_no_match_and_empty_term_are_empty_answers_not_errors():
    tool = MemoryRecallTool(store=_Store([_rec(1, "something else")]))
    assert tool.run(term="absent")["count"] == 0
    assert tool.run(term="   ")["count"] == 0


def test_an_unreadable_store_is_unavailable_not_empty_memory():
    out = MemoryRecallTool(store=_Store([], raises=True)).run(term="x")
    assert out["count"] == 0 and out["unavailable"].startswith("OSError")


def test_the_tool_is_read_only_and_strict_about_arguments():
    tool = MemoryRecallTool(store=_Store([]))
    assert tool.risk == "read_only"
    with pytest.raises(PermissionError):
        tool.run(term="x", limit=50)
    with pytest.raises(ValueError, match="requires"):
        tool.run()


def test_the_door_is_wired_and_classified_for_the_unattended_path():
    import inspect

    from app import bootstrap
    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS

    assert "MemoryRecallTool(" in inspect.getsource(bootstrap.build_agent)
    assert "memory_recall" not in _AUTONOMOUS_GOAL_BLOCKED_TOOLS
