"""Unit tests for WorkingMemory (§4 Memory & Knowledge Governance).

Covers the two views the rest of the loop relies on:
  - conversation log: record_turn / recent_turns / bounded retention / clear
  - artifact cache:   cache_key stability / lookup-miss / lookup-hit / clear
"""
from __future__ import annotations

from core.memory import WorkingMemory

# ---------- conversation log ----------


def test_max_turns_drops_oldest() -> None:
    mem = WorkingMemory(max_turns=3)
    for i in range(5):
        mem.record_turn(f"q{i}", "", [], [], f"a{i}")

    # Only the last 3 survive
    assert len(mem.turns) == 3
    assert [t.index for t in mem.turns] == [3, 4, 5]


def test_conversation_context_respects_char_cap() -> None:
    mem = WorkingMemory(max_turns=20, max_context_chars=300)
    long_answer = "x" * 4_000
    for i in range(5):
        mem.record_turn(f"q{i}", "", [], [], long_answer)

    ctx = mem.conversation_context(max_turns=5)
    assert len(ctx) <= 300


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


# ---------- max_context_tokens ----------

def test_conversation_context_respects_token_cap() -> None:
    """When max_context_tokens is tighter than max_context_chars, it wins."""
    # 10 tokens → 40 chars effective cap; set chars cap much higher so tokens cap bites
    mem = WorkingMemory(max_turns=20, max_context_chars=100_000, max_context_tokens=10)
    long_answer = "word " * 200  # ~200 words >> 10 tokens
    for i in range(3):
        mem.record_turn(f"q{i}", "", [], [], long_answer)

    ctx = mem.conversation_context(max_turns=3)
    # 10 tokens * 4 chars/token = 40 chars effective cap
    assert len(ctx) <= 40


# ---------- max_artifacts ----------

def test_max_artifacts_evicts_oldest_when_exceeded() -> None:
    """Cache must not grow beyond max_artifacts entries."""
    mem = WorkingMemory(max_artifacts=3)
    for i in range(5):
        mem.cache_store("tool", {"i": i}, f"output_{i}", f"label_{i}")

    assert len(mem.artifacts) == 3
    # The 3 newest entries (i=2,3,4) should be present
    assert mem.cache_lookup("tool", {"i": 2}) is not None
    assert mem.cache_lookup("tool", {"i": 4}) is not None
    # The 2 oldest (i=0,1) should have been evicted
    assert mem.cache_lookup("tool", {"i": 0}) is None
    assert mem.cache_lookup("tool", {"i": 1}) is None
