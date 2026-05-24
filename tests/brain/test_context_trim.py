"""Tests for ContextBuilder budget enforcement (`_trim_to_budget`)."""

from __future__ import annotations

import pytest

from brain.context_builder import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_TOKENS,
    _length,
    _trim_messages,
    _trim_to_budget,
)


def _h(n: int, char: str = "h") -> list[dict]:
    """Build a list of history-style messages each ~100 chars."""
    return [{"role": "user", "content": char * 100} for _ in range(n)]


def _f(n: int, char: str = "f") -> list[dict]:
    """Build a list of fact-style records each ~100 chars."""
    return [{"text": char * 100, "score": 1.0 - i * 0.01} for i in range(n)]


# ────────────────────────────────────────────────────────────────────


def test_small_payload_passes_through_untouched():
    history = _h(3)
    facts = _f(3)
    h_out, f_out = _trim_to_budget("hello", history, facts)
    assert h_out == history
    assert f_out == facts


def test_huge_history_is_trimmed_keeping_most_recent():
    # Each history row is 100 chars. With MAX_CONTEXT_CHARS = 32000 and
    # 40% reserved for history → ~13700 chars → ~137 rows max. Give it 500.
    history = _h(500)
    facts = []
    h_out, _ = _trim_to_budget("x", history, facts)
    assert len(h_out) < 500
    # Last item must be preserved (most recent)
    assert h_out[-1] is history[-1]
    # First items dropped — head of trimmed list is NOT the first of original
    assert h_out[0] is not history[0]


def test_huge_facts_are_trimmed_keeping_highest_ranked():
    facts = _f(500)
    h_out, f_out = _trim_to_budget("x", [], facts)
    assert len(f_out) < 500
    # First fact (highest score) must survive
    assert f_out[0] is facts[0]


def test_oversized_input_still_returns_at_least_one_per_section():
    """Pathological case: input alone blows the whole budget.

    We must not crash — and we keep at least one grounding row each.
    """
    giant_input = "x" * (MAX_CONTEXT_CHARS + 10_000)
    history = _h(3)
    facts = _f(3)
    h_out, f_out = _trim_to_budget(giant_input, history, facts)
    assert len(h_out) == 1
    assert len(f_out) == 1


def test_max_context_constants_relationship():
    """Document and lock the relationship between tokens and chars."""
    assert MAX_CONTEXT_CHARS == MAX_CONTEXT_TOKENS * 4


def test_trim_messages_with_zero_budget_returns_empty():
    out = _trim_messages(_h(3), 0, keep_recent=True)
    assert out == []


def test_trim_messages_always_keeps_at_least_one_when_budget_allows():
    """Even if first item alone exceeds the cap, we keep it to avoid empties."""
    items = [{"content": "x" * 1000}]
    out = _trim_messages(items, 100, keep_recent=True)
    assert len(out) == 1


def test_length_handles_missing_keys():
    assert _length({}) == 0
    assert _length({"content": "abcd"}) == 4
    assert _length({"text": "xyz"}) == 3
    assert _length({"text": None}) == 0


def test_trim_messages_keep_recent_returns_tail_in_order():
    items = [{"content": f"msg{i}"} for i in range(10)]
    out = _trim_messages(items, 20, keep_recent=True)
    # We don't care exactly how many — but they must be tail-most AND in chronological order
    assert all(out[i]["content"] < out[i + 1]["content"] for i in range(len(out) - 1))
    assert out[-1] == items[-1]


def test_trim_messages_keep_head_returns_head_in_order():
    items = [{"text": f"fact{i}"} for i in range(10)]
    out = _trim_messages(items, 20, keep_recent=False)
    assert out[0] == items[0]
