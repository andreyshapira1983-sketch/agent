"""Tests for core.compactor (Anthropic 2025 context-engineering compaction)."""

from datetime import datetime, timezone

from core.compactor import compact_turns, default_summarizer
from core.memory import Turn, WorkingMemory


def _make_turn(idx: int, q: str = "q", ans: str = "a", tools=None, labels=None) -> Turn:
    return Turn(
        index=idx,
        id=f"turn_{idx}",
        timestamp=datetime.now(timezone.utc),
        question=q,
        planner_reasoning="",
        tools_used=list(tools or []),
        artifact_labels=list(labels or []),
        answer=ans,
    )


def test_compact_noop_when_below_keep_recent():
    turns = [_make_turn(i) for i in range(2)]
    new_turns, summary = compact_turns(turns, keep_recent=3)
    assert summary is None
    assert new_turns == turns


def test_compact_folds_older_into_summary():
    turns = [_make_turn(i, q=f"q{i}", ans=f"a{i}") for i in range(1, 7)]
    new_turns, summary = compact_turns(turns, keep_recent=3)
    assert summary is not None
    # 1 summary + 3 recent
    assert len(new_turns) == 4
    assert new_turns[0] is summary
    assert [t.index for t in new_turns[1:]] == [4, 5, 6]
    # Summary holds the index of the LAST dropped turn (3).
    assert summary.index == 3
    assert "turn 1" in summary.answer
    assert "turn 3" in summary.answer
    assert summary.question == "[compacted history]"


def test_compact_dedupes_tools_and_labels():
    turns = [
        _make_turn(1, tools=["file_read"], labels=["f:a"]),
        _make_turn(2, tools=["file_read", "web_search"], labels=["f:a", "w:1"]),
        _make_turn(3, tools=["web_search"], labels=["w:1"]),
        _make_turn(4),
        _make_turn(5),
        _make_turn(6),
    ]
    _, summary = compact_turns(turns, keep_recent=3)
    assert summary is not None
    assert summary.tools_used == ["file_read", "web_search"]
    assert summary.artifact_labels == ["f:a", "w:1"]


def test_default_summarizer_truncates_long_text():
    long_q = "x" * 500
    long_a = "y" * 500
    turns = [_make_turn(1, q=long_q, ans=long_a)]
    out = default_summarizer(turns)
    # Per-line truncation caps q at ~140 and ans at ~200.
    assert "…" in out
    assert "x" * 500 not in out
    assert "y" * 500 not in out


def test_custom_summarizer_used():
    turns = [_make_turn(i) for i in range(1, 6)]
    seen: list[int] = []

    def custom(items):
        seen.append(len(items))
        return "custom-summary"

    _, summary = compact_turns(turns, keep_recent=2, summarizer=custom)
    assert summary is not None
    assert summary.answer == "custom-summary"
    assert seen == [3]


def test_compact_keep_recent_zero_summarizes_all():
    turns = [_make_turn(i) for i in range(1, 4)]
    new_turns, summary = compact_turns(turns, keep_recent=0)
    assert summary is not None
    assert new_turns == [summary]


def test_compact_negative_keep_recent_raises():
    import pytest
    with pytest.raises(ValueError):
        compact_turns([_make_turn(1)], keep_recent=-1)


# ---------- WorkingMemory integration ----------


def test_compact_if_needed_no_op_below_threshold():
    mem = WorkingMemory(max_turns=10)
    mem.record_turn("q1", "", [], [], "a1")
    mem.record_turn("q2", "", [], [], "a2")
    assert mem.compact_if_needed() is False
    assert len(mem.turns) == 2


def test_compact_if_needed_fires_when_above_default_threshold():
    # Default threshold = max_turns - 1 = 4
    mem = WorkingMemory(max_turns=5)
    for i in range(1, 6):
        mem.record_turn(f"q{i}", "", ["file_read"], [f"f:{i}"], f"a{i}")
    fired = mem.compact_if_needed(keep_recent=2)
    assert fired is True
    # 1 summary + 2 recent
    assert len(mem.turns) == 3
    assert mem.turns[0].question == "[compacted history]"
    assert [t.index for t in mem.turns[1:]] == [4, 5]


def test_compact_if_needed_explicit_threshold():
    mem = WorkingMemory(max_turns=20)
    for i in range(1, 6):
        mem.record_turn(f"q{i}", "", [], [], f"a{i}")
    fired = mem.compact_if_needed(threshold=3, keep_recent=2)
    assert fired is True
    assert len(mem.turns) == 3
