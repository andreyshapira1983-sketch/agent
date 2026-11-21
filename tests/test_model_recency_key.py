"""Tests for _model_recency_key — date/version-aware model ranking."""

from core.model_catalog import _model_recency_key

# ── max() over real catalog data returns the correct model ────────────────────

def test_max_openai_deep():
    """o4-mini-2025-04-16 beats o1, o3-mini and the same-date o3.

    The major version ranks first in the key: 4 > 3 whatever the date or suffix.
    """
    candidates = ["o1-2024-12-17", "o3-mini-2025-01-31", "o4-mini-2025-04-16", "o3-2025-04-16"]
    best = max(candidates, key=_model_recency_key)
    assert best == "o4-mini-2025-04-16"


# ── edge cases ────────────────────────────────────────────────────────────────


def test_iso_date_parsed_correctly():
    """2026-03-17 → year=2026, month=3, day=17 (at positions 2-4 of key)."""
    key = _model_recency_key("gpt-5.4-nano-2026-03-17")
    assert key[2:5] == (2026, 3, 17)
