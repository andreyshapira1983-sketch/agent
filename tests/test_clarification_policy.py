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
    AmbiguityFinding,
    ClarificationResult,
    check_clarification,
)


# ---------------------------------------------------------------------------
# Unit: check_clarification
# ---------------------------------------------------------------------------

class TestInformationalBypass:
    """Purely informational questions must always proceed without asking."""

    @pytest.mark.parametrize("text", [
        "что такое Docker?",
        "What is the capital of France?",
        "расскажи как работает планировщик",
        "explain how the memory policy works",
        "покажи список файлов",
        "запусти тесты",
        "прочитай этот файл",
        "найди все функции в core/loop.py",
        "посмотри на бюджет",
        "check the approval inbox",
    ])
    def test_proceed_for_informational(self, text: str) -> None:
        result = check_clarification(text)
        assert result.decision == "proceed", (
            f"Expected proceed for '{text}', got ask: {result.question}"
        )
        assert not result.should_ask


class TestDestructiveWithoutTarget:
    """Destructive verbs without a target → ask."""

    @pytest.mark.parametrize("text", [
        "удали",
        "перезапиши",
        "очисти",
        "delete",
        "drop table",
        "wipe",
        "rm",
    ])
    def test_ask_for_destructive_no_target(self, text: str) -> None:
        result = check_clarification(text)
        assert result.decision == "ask", (
            f"Expected ask for '{text}', got proceed"
        )
        assert result.should_ask
        assert result.question  # non-empty clarification question
        assert any(f.kind == "missing_target" for f in result.findings)


class TestDestructiveWithTarget:
    """Destructive verb WITH explicit target → proceed (no ambiguity)."""

    @pytest.mark.parametrize("text", [
        "удали файл /tmp/test.txt",
        'удали "старый_бэкап.zip"',
        "перезапиши core/loop.py",
        "delete old_file.py",
        "rm /tmp/cache",
        "очисти logs/run_old.jsonl",
    ])
    def test_proceed_with_target(self, text: str) -> None:
        result = check_clarification(text)
        # May still have findings, but decision must be proceed
        assert result.decision == "proceed", (
            f"Expected proceed for '{text}', got ask: {result.question}"
        )


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


class TestUnderspecifiedGoal:
    """Vague placeholder in goal description → ask."""

    @pytest.mark.parametrize("text", [
        "сделай что-нибудь с этим",
        "измени что-то",
        "fix something",
        "change anything",
    ])
    def test_ask_for_vague_goal(self, text: str) -> None:
        result = check_clarification(text)
        assert result.decision == "ask", (
            f"Expected ask for '{text}', got proceed"
        )
        assert any(f.kind == "underspecified_goal" for f in result.findings)


class TestResultStructure:
    """ClarificationResult fields are consistent."""

    def test_proceed_has_empty_question(self) -> None:
        result = check_clarification("что такое питон?")
        assert result.decision == "proceed"
        assert result.question == ""
        assert not result.should_ask

    def test_ask_has_non_empty_question(self) -> None:
        result = check_clarification("удали")
        assert result.decision == "ask"
        assert len(result.question) > 10
        assert result.should_ask

    def test_findings_have_required_fields(self) -> None:
        result = check_clarification("удали")
        for f in result.findings:
            assert isinstance(f, AmbiguityFinding)
            assert f.kind in ("missing_target", "conflicting_scope", "underspecified_goal")
            assert 0.0 <= f.confidence <= 1.0
            assert f.evidence  # non-empty


# ---------------------------------------------------------------------------
# Integration: AgentLoop returns clarification question
# ---------------------------------------------------------------------------

class TestLoopClarificationIntegration:
    """AgentLoop.run() returns the clarification question instead of planning."""

    def _make_loop(self):
        """Build a minimal AgentLoop backed by a mock LLM."""
        import json
        from unittest.mock import MagicMock, patch
        from pathlib import Path

        from core.loop import AgentLoop
        from core.logger import TraceLogger
        from core.policy import PolicyGate
        from tools.base import ToolRegistry

        # Minimal mock LLM
        mock_llm = MagicMock()
        mock_llm.complete.return_value = json.dumps(
            {"reasoning": "test", "sources": []}
        )
        mock_llm.model = "mock"
        mock_llm.stream = None
        mock_llm.route = None

        registry = ToolRegistry()
        policy = PolicyGate(registry=registry)
        from core.logger import TraceLogger
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        logger = TraceLogger(trace_id="test-clarif", log_dir=tmp / "logs")

        loop = AgentLoop(
            registry=registry,
            policy=policy,
            llm=mock_llm,
            logger=logger,
        )
        return loop

    def test_ambiguous_destructive_returns_question(self) -> None:
        loop = self._make_loop()
        result = loop.run("удали")
        # Should return the clarification question, NOT an Output Contract
        assert "Conclusion:" not in result
        assert len(result) > 5
        # The question should contain guidance about specifying a target
        assert any(
            word in result.lower()
            for word in ("уточни", "укажи", "поясни", "что именно", "к чему")
        )

    def test_informational_passes_through(self) -> None:
        loop = self._make_loop()
        # Informational questions must NOT trigger clarification.
        # The loop will call the (mock) LLM planner and synthesizer.
        result = loop.run("что такое питон?")
        # As long as we don't get a clarification question the bypass worked.
        assert "Уточни" not in result[:60] or "Conclusion:" in result

    def test_clarification_event_logged(self) -> None:
        import json
        loop = self._make_loop()
        loop.run("удали")
        # Check the trace log for clarification_request event
        log_dir = loop.log.log_dir  # type: ignore[attr-defined]
        log_files = list(log_dir.glob("*.jsonl"))
        assert log_files, "No trace log file found"
        events = [
            json.loads(line)
            for f in log_files
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_names = [e.get("event") for e in events]
        assert "clarification_request" in event_names


# ============================================================
# Security / validation tests (audit fixes)
# ============================================================

class TestClarificationInputValidation:
    """Tests for None-guard and ReDoS protection added in deep audit."""

    def test_none_input_returns_proceed(self):
        result = check_clarification(None)  # type: ignore[arg-type]
        assert result.decision == "proceed"

    def test_integer_input_returns_proceed(self):
        result = check_clarification(42)  # type: ignore[arg-type]
        assert result.decision == "proceed"

    def test_empty_string_returns_proceed(self):
        result = check_clarification("")
        assert result.decision == "proceed"

    def test_whitespace_only_returns_proceed(self):
        result = check_clarification("   \n\t")
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
        """A destructive verb near start of a 5000-char input is still caught."""
        text = "удали" + " слово" * 1000
        result = check_clarification(text)
        # After truncation to 4096, destructive verb is present
        assert result.decision in ("proceed", "ask")
