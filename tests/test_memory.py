"""Unit tests for WorkingMemory (§4 Memory & Knowledge Governance).

Covers the two views the rest of the loop relies on:
  - conversation log: record_turn / recent_turns / bounded retention / clear
  - artifact cache:   cache_key stability / lookup-miss / lookup-hit / clear
"""
from __future__ import annotations

from core.memory import Turn, WorkingMemory


# ---------- conversation log ----------

def test_record_turn_assigns_monotonic_indexes() -> None:
    mem = WorkingMemory()
    t1 = mem.record_turn("q1", "r1", ["file_read"], ["file:a"], "a1")
    t2 = mem.record_turn("q2", "r2", [], [], "a2")

    assert t1.index == 1
    assert t2.index == 2
    assert t1.id != t2.id
    assert mem.turns == [t1, t2]


def test_recent_turns_returns_tail() -> None:
    mem = WorkingMemory()
    for i in range(4):
        mem.record_turn(f"q{i}", "", [], [], f"a{i}")
    last_two = mem.recent_turns(2)
    assert [t.index for t in last_two] == [3, 4]


def test_max_turns_drops_oldest() -> None:
    mem = WorkingMemory(max_turns=3)
    for i in range(5):
        mem.record_turn(f"q{i}", "", [], [], f"a{i}")

    # Only the last 3 survive
    assert len(mem.turns) == 3
    assert [t.index for t in mem.turns] == [3, 4, 5]


def test_conversation_context_empty_when_no_turns() -> None:
    mem = WorkingMemory()
    assert mem.conversation_context() == ""


def test_conversation_context_renders_recent_turns() -> None:
    mem = WorkingMemory()
    mem.record_turn(
        question="What is DuckDuckGo?",
        planner_reasoning="external info",
        tools_used=["web_search"],
        artifact_labels=["web:DuckDuckGo"],
        answer="A privacy-focused search engine.",
    )

    ctx = mem.conversation_context()
    assert "Turn 1:" in ctx
    assert "user: What is DuckDuckGo?" in ctx
    assert "tools_used: web_search" in ctx
    assert "A privacy-focused search engine." in ctx


def test_conversation_context_respects_char_cap() -> None:
    mem = WorkingMemory(max_turns=20, max_context_chars=300)
    long_answer = "x" * 4_000
    for i in range(5):
        mem.record_turn(f"q{i}", "", [], [], long_answer)

    ctx = mem.conversation_context(max_turns=5)
    assert len(ctx) <= 300


def test_turn_summary_truncates_long_answers() -> None:
    turn = Turn(
        index=1,
        id="turn_xxx",
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        question="q",
        planner_reasoning="",
        tools_used=[],
        artifact_labels=[],
        answer="word " * 2_000,
    )
    summary = turn.summary(max_chars=100)
    assert summary.endswith("…")
    # 100-char cap plus the prefix lines ("Turn 1:\n  user: q\n  tools_used: (none)\n  agent_answer: ")
    assert "agent_answer:" in summary


# ---------- artifact cache ----------

def test_cache_key_is_stable_across_arg_order() -> None:
    a = WorkingMemory.cache_key("web_search", {"query": "X", "max_results": 5})
    b = WorkingMemory.cache_key("web_search", {"max_results": 5, "query": "X"})
    assert a == b


def test_cache_key_differs_on_value_change() -> None:
    a = WorkingMemory.cache_key("file_read", {"path": "doc.txt"})
    b = WorkingMemory.cache_key("file_read", {"path": "other.txt"})
    assert a != b


def test_cache_lookup_miss_returns_none() -> None:
    mem = WorkingMemory()
    assert mem.cache_lookup("file_read", {"path": "missing.txt"}) is None


def test_cache_store_then_lookup_returns_entry() -> None:
    mem = WorkingMemory()
    mem.cache_store(
        tool_name="file_read",
        arguments={"path": "doc.txt"},
        output="contents of doc",
        label="file:doc.txt",
    )

    hit = mem.cache_lookup("file_read", {"path": "doc.txt"})
    assert hit is not None
    assert hit["tool"] == "file_read"
    assert hit["output"] == "contents of doc"
    assert hit["label"] == "file:doc.txt"
    assert hit["arguments"] == {"path": "doc.txt"}


def test_cache_is_isolated_per_tool() -> None:
    """A web_search query and a file_read with same string value do NOT collide."""
    mem = WorkingMemory()
    mem.cache_store("file_read", {"path": "alpha"}, "F", "file:alpha")
    mem.cache_store("web_search", {"query": "alpha", "max_results": 5}, "W", "web:alpha")

    assert mem.cache_lookup("file_read", {"path": "alpha"})["output"] == "F"
    assert mem.cache_lookup("web_search", {"query": "alpha", "max_results": 5})["output"] == "W"


# ---------- clear ----------

def test_clear_wipes_turns_and_cache() -> None:
    mem = WorkingMemory()
    mem.record_turn("q", "", ["file_read"], ["file:x"], "answer")
    mem.cache_store("file_read", {"path": "x"}, "out", "file:x")

    assert len(mem.turns) == 1
    assert mem.cache_lookup("file_read", {"path": "x"}) is not None

    mem.clear()

    assert mem.turns == []
    assert mem.cache_lookup("file_read", {"path": "x"}) is None
    assert mem.summary()["turns"] == 0
    assert mem.summary()["artifacts_cached"] == 0


# ---------- summary ----------

def test_summary_reports_session_state() -> None:
    mem = WorkingMemory(max_turns=7, max_context_chars=1234)
    mem.record_turn("q1", "", ["web_search"], ["web:foo"], "ans1")
    mem.cache_store("web_search", {"query": "foo", "max_results": 5}, [], "web:foo")

    snap = mem.summary()
    assert snap["session_id"] == mem.session_id
    assert snap["turns"] == 1
    assert snap["artifacts_cached"] == 1
    assert snap["max_turns"] == 7
    assert snap["max_context_chars"] == 1234
    assert snap["labels"] == [{"label": "web:foo", "tool": "web_search", "turn": 2}]
