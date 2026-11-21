"""Tests for core/clarification_policy.py (§3 Clarification Policy).

Coverage targets:
  - Informational bypass (should never trigger clarification)
  - Destructive verb without target → ask
  - Destructive verb WITH target → proceed
  - Conflicting scope → ask
  - Underspecified goal → ask
  - Loop integration: AgentLoop returns clarification question, not plan output
"""
from __future__ import annotations

import pytest

from core.clarification_policy import (
    check_clarification,
)

# ---------------------------------------------------------------------------
# Unit: check_clarification
# ---------------------------------------------------------------------------


class TestConflictingScope:
    """Contradictory scope signals → ask."""

    @pytest.mark.parametrize("text", [
        "сделай для всех файлов кроме всех",
        "always do this except always",
        "применить везде кроме везде",
    ])
    def test_ask_for_conflicting_scope(self, text: str) -> None:
        result = check_clarification(text)
        assert result.decision == "ask", (
            f"Expected ask for '{text}', got proceed"
        )
        assert any(f.kind == "conflicting_scope" for f in result.findings)


# ============================================================
# Security / validation tests (audit fixes)
# ============================================================

class TestClarificationInputValidation:
    """Tests for None-guard and ReDoS protection added in deep audit."""


    def test_integer_input_returns_proceed(self):
        result = check_clarification(42)  # type: ignore[arg-type]
        assert result.decision == "proceed"

    def test_empty_string_returns_proceed(self):
        result = check_clarification("")
        assert result.decision == "proceed"


    def test_very_long_input_returns_quickly(self):
        """Inputs exceeding 4096 chars must be truncated, not hang."""
        import time
        long_text = "a" * 500_000
        start = time.monotonic()
        result = check_clarification(long_text)
        elapsed = time.monotonic() - start
        # Should complete in well under 1 second even on slow machines
        assert elapsed < 2.0, f"Too slow on large input: {elapsed:.2f}s"
        assert result.decision in ("proceed", "ask")

    def test_long_input_with_destructive_verb_truncated(self):
        """A destructive verb at the start of a 6000-char input survives the 4096 cut."""
        text = "удали" + " слово" * 1000
        result = check_clarification(text)
        assert result.decision == "ask"
        assert [(f.kind, f.evidence) for f in result.findings] == [("missing_target", "удали")]
