"""Tests for _model_recency_key — date/version-aware model ranking."""
import pytest
from core.model_catalog import _model_recency_key


# ── parametrized: newer model must have a larger key than older ───────────────

@pytest.mark.parametrize("newer, older", [
    # ISO dates: gpt with newer date wins
    ("gpt-5.4-nano-2026-03-17",        "gpt-4o-mini-2025-07-18"),
    # ISO date vs compact date
    ("o4-mini-2025-04-16",             "o1-2024-12-17"),
    # Compact date: bigger date wins
    ("claude-haiku-4-5-20251001",      "claude-haiku-3-5-20240307"),
    # Both compact: month matters
    ("claude-opus-4-8-20261101",       "claude-opus-4-8-20261001"),
    # Version numbers (no date): major.minor
    ("claude-opus-4-8",                "claude-opus-4-5"),
    ("claude-sonnet-4-6",              "claude-sonnet-4-5-20250929"),  # 4-6 no date > 4-5 with old date
    # Same family, newer generation
    ("claude-opus-4-8",                "claude-opus-3-5"),
    # ISO date wins over version-only
    ("gpt-5.4-nano-2026-03-17",        "claude-opus-4-8"),
])
def test_newer_beats_older(newer, older):
    assert _model_recency_key(newer) > _model_recency_key(older), (
        f"Expected {newer!r} > {older!r}"
    )


# ── max() over real catalog data returns the correct model ────────────────────

def test_max_openai_deep():
    """o4-mini-2025-04-16 should beat o1-2024-12-17 and o3-mini-2025-01-31."""
    candidates = ["o1-2024-12-17", "o3-mini-2025-01-31", "o4-mini-2025-04-16", "o3-2025-04-16"]
    best = max(candidates, key=_model_recency_key)
    # o4-mini and o3 both have 2025-04-16 — o4 > o3 lexicographically as fallback
    assert best in {"o4-mini-2025-04-16", "o3-2025-04-16"}


def test_max_anthropic_deep():
    """claude-opus-4-8 should beat older opus models."""
    candidates = [
        "claude-opus-4-20250514",
        "claude-opus-4-5-20251101",
        "claude-opus-4-6",
        "claude-opus-4-8",
    ]
    best = max(candidates, key=_model_recency_key)
    assert best == "claude-opus-4-8"


def test_max_anthropic_light():
    """Newest haiku by date."""
    candidates = ["claude-haiku-4-5-20251001", "claude-haiku-3-5-20240307"]
    best = max(candidates, key=_model_recency_key)
    assert best == "claude-haiku-4-5-20251001"


def test_max_anthropic_standard():
    """claude-sonnet-4-6 should beat older sonnet versions."""
    candidates = [
        "claude-sonnet-4-20250514",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6",
    ]
    best = max(candidates, key=_model_recency_key)
    assert best == "claude-sonnet-4-6"


# ── edge cases ────────────────────────────────────────────────────────────────

def test_single_candidate():
    candidates = ["gpt-4o-mini"]
    assert max(candidates, key=_model_recency_key) == "gpt-4o-mini"


def test_no_date_no_version_fallback():
    """Pure lexicographic fallback when no date or version in name."""
    candidates = ["gpt-4", "gpt-3"]
    best = max(candidates, key=_model_recency_key)
    assert best == "gpt-4"


def test_same_date_tiebreak_by_name():
    """Two models with the same date: lexicographic tiebreak on model_id."""
    candidates = ["o4-mini-2025-04-16", "o3-2025-04-16"]
    best = max(candidates, key=_model_recency_key)
    # Both (2025,4,16,0,0,name) → tiebreak on name: "o4-mini" > "o3"
    assert best == "o4-mini-2025-04-16"


def test_compact_date_parsed_correctly():
    """20251001 → year=2025, month=10, day=1 (at positions 2-4 of key)."""
    key = _model_recency_key("claude-haiku-4-5-20251001")
    assert key[2:5] == (2025, 10, 1)


def test_iso_date_parsed_correctly():
    """2026-03-17 → year=2026, month=3, day=17 (at positions 2-4 of key)."""
    key = _model_recency_key("gpt-5.4-nano-2026-03-17")
    assert key[2:5] == (2026, 3, 17)


def test_version_parsed_correctly():
    """claude-opus-4-8 → major=4, minor=8 (no date)."""
    key = _model_recency_key("claude-opus-4-8")
    assert key == (4, 8, 0, 0, 0, "claude-opus-4-8")
