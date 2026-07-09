"""Tests for core/evidence_budget.py — per-artifact + total evidence budget."""
from __future__ import annotations

import pytest

from core.evidence_budget import (
    EVIDENCE_FILE_CHARS,
    EVIDENCE_TOTAL_CHARS,
    apply_total_budget,
    budget_file_content,
    extract_relevant,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_file(n_chars: int, keyword: str = "alpha") -> str:
    """Return a synthetic file of ~n_chars with *keyword* near the middle."""
    half = n_chars // 2
    filler_a = ("x " * 100 + "\n\n")
    filler_b = ("y " * 100 + "\n\n")
    head  = (filler_a * (half // len(filler_a) + 1))[:half]
    mid   = f"\n\n# Section with {keyword}\n\nThis section discusses {keyword} in detail.\n\n"
    tail  = (filler_b * (half // len(filler_b) + 1))[:half]
    return head + mid + tail


# ── extract_relevant — basic contract ────────────────────────────────────────

def test_short_text_returned_unchanged():
    text = "Hello world"
    assert extract_relevant(text, question="hello", budget=200) == text


def test_returns_at_most_budget_chars():
    text = _make_file(50_000, keyword="budget")
    result = extract_relevant(text, question="budget governor config", budget=5_000)
    # The notice adds a little extra; allow 10% headroom for the notice string
    assert len(result) <= 5_000 * 1.1


def test_keyword_section_is_preferred():
    """The paragraph containing the question keyword must appear in the result."""
    text = _make_file(30_000, keyword="governor")
    result = extract_relevant(text, question="how does the governor work?", budget=3_000)
    assert "governor" in result


def test_budget_notice_is_appended():
    text = _make_file(20_000, keyword="alpha")
    result = extract_relevant(text, question="alpha", budget=2_000)
    assert "INTENT-BUDGET" in result


def test_no_keyword_match_fallback():
    """When question has no overlap, head+tail fallback is used."""
    text = "alpha " * 5_000   # 30 000 chars, no keyword matching "zebra"
    result = extract_relevant(text, question="zebra", budget=3_000)
    assert "INTENT-BUDGET" in result
    # Head should start same as original
    assert result.startswith("alpha")


def test_empty_text_returned_unchanged():
    assert extract_relevant("", question="anything", budget=1_000) == ""


def test_budget_zero_returns_empty():
    assert extract_relevant("hello world", question="hello", budget=0) == ""


def test_first_paragraph_always_included():
    """Paragraph 0 (file preamble) must always appear in the result."""
    intro = "# Module intro\nThis file describes the system.\n\n"
    body  = "\n\n".join([f"# Section {i}\nContent about topic_{i}." for i in range(40)])
    text  = intro + body
    result = extract_relevant(text, question="topic_20 topic_21", budget=2_000)
    assert "Module intro" in result


# ── extract_relevant — Realtime Intent property ───────────────────────────────

def test_intent_shapes_selection():
    """Different questions yield different excerpts from the same file."""
    sections = [
        "# Authentication\n\nThe auth module handles JWT tokens and OAuth2.",
        "# Budget\n\nThe budget governor limits LLM calls per hour.",
        "# Logging\n\nAll events are written to JSONL files in the logs/ directory.",
        "# Deployment\n\nUse Docker Compose for local development.",
    ]
    text = "\n\n".join(sections * 20)   # ~8 000 chars

    result_auth   = extract_relevant(text, question="how does JWT authentication work?", budget=800)
    result_budget = extract_relevant(text, question="how does budget governor work?",    budget=800)

    assert "Authentication" in result_auth or "auth" in result_auth.lower()
    assert "Budget" in result_budget or "budget" in result_budget.lower()
    # The two results should differ (different intent → different selection)
    assert result_auth != result_budget


def test_cyrillic_keywords_work():
    """Cyrillic question keywords must match Cyrillic content."""
    text = (
        "# Раздел о бюджете\n\nБюджет ограничивает количество вызовов LLM в час.\n\n"
        + "# Other section\n\nThis is about something else entirely.\n\n" * 50
    )
    result = extract_relevant(text, question="как работает бюджет?", budget=3_000)
    assert "бюджет" in result.lower()


# ── budget_file_content ───────────────────────────────────────────────────────

def test_budget_file_content_passthrough_small():
    small = "x" * 100
    assert budget_file_content(small, question="test") == small


def test_budget_file_content_truncates_large(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_FILE_CHARS", "500")
    large = _make_file(10_000, keyword="alpha")
    result = budget_file_content(large, question="alpha")
    # Must be capped — allow 30% headroom for the INTENT-BUDGET notice string
    assert len(result) <= 700


def test_budget_file_content_default_limit_is_sane():
    """Default EVIDENCE_FILE_CHARS must be > 0 and < typical model context window."""
    assert 1_000 < EVIDENCE_FILE_CHARS < 100_000


# ── apply_total_budget ────────────────────────────────────────────────────────

def test_apply_total_budget_no_trim_needed():
    blocks = [("file:a.py", "x" * 100), ("file:b.py", "y" * 200)]
    result, was_trimmed = apply_total_budget(blocks)
    assert not was_trimmed
    assert len(result) == 2
    assert result[0][1] == "x" * 100


def test_apply_total_budget_trims_largest_first(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    # Block A: 300 chars, Block B: 200 chars → total 500 > 400
    blocks = [("a", "A" * 300), ("b", "B" * 200)]
    result, was_trimmed = apply_total_budget(blocks)
    assert was_trimmed
    total = sum(len(c) for _, c in result)
    assert total <= 450   # 400 + some notice chars


def test_apply_total_budget_adds_notice(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "200")
    blocks = [("file:big.md", "Z" * 500)]
    result, was_trimmed = apply_total_budget(blocks)
    assert was_trimmed
    assert "TOTAL-BUDGET" in result[0][1]


def test_apply_total_budget_preserves_small_blocks(monkeypatch):
    """The small block must be returned intact when the large one is trimmed."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "600")
    small_content = "s" * 100
    blocks = [("big", "B" * 700), ("small", small_content)]
    result, was_trimmed = apply_total_budget(blocks)
    assert was_trimmed
    # Find the small block by label
    small_result = next(c for lbl, c in result if lbl == "small")
    assert small_result == small_content   # small block untouched


def test_apply_total_budget_empty_list():
    result, was_trimmed = apply_total_budget([])
    assert result == []
    assert not was_trimmed


def test_apply_total_budget_single_block_fits(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1000")
    blocks = [("x", "a" * 999)]
    result, was_trimmed = apply_total_budget(blocks)
    assert not was_trimmed
    assert result[0][1] == "a" * 999


def test_default_total_budget_is_sane():
    assert 5_000 < EVIDENCE_TOTAL_CHARS < 500_000


# ── integration: _format_artifact via loop ───────────────────────────────────

def test_format_artifact_small_file_unchanged():
    """Files smaller than the per-artifact budget pass through untouched."""
    from core.loop import AgentLoop
    small = "# README\n\nShort file.\n"
    result = AgentLoop._format_artifact("file_read", small, question="readme")
    assert result == small


def test_format_artifact_large_file_truncated(monkeypatch):
    """Files larger than AGENT_EVIDENCE_FILE_CHARS are truncated."""
    from core.loop import AgentLoop
    monkeypatch.setenv("AGENT_EVIDENCE_FILE_CHARS", "300")
    large = _make_file(5_000, keyword="governor")
    result = AgentLoop._format_artifact(
        "file_read", large, question="how does the governor work?"
    )
    # budget=300; allow ~120 chars of overhead (notice string + separators)
    assert len(result) <= 420


def test_format_artifact_web_search_unchanged():
    """Web search results are NOT subject to the file budget."""
    from core.loop import AgentLoop
    hits = [{"title": "A", "url": "http://x.com", "snippet": "s", "source": "ddg"}]
    result = AgentLoop._format_artifact("web_search", hits, question="any question")
    assert "http://x.com" in result
