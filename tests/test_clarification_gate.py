"""Tests for core.clarification_gate — режим переспроса (ask, don't build)."""
from __future__ import annotations

from core.clarification_gate import (
    FORBIDDEN_IN_CLARIFY,
    ClarificationSignals,
    evaluate,
)


class TestProceed:
    def test_no_signals_proceeds(self):
        out = evaluate(ClarificationSignals())
        assert out.mode == "proceed"
        assert out.should_clarify is False
        assert out.allowed_action is None
        assert out.forbidden_actions == ()
        assert out.questions == ()

    def test_proceed_forbids_nothing(self):
        out = evaluate(ClarificationSignals())
        # In proceed mode the chaos actions are all permitted.
        for action in FORBIDDEN_IN_CLARIFY:
            assert out.is_forbidden(action) is False

    def test_proceed_prompt_is_empty(self):
        assert evaluate(ClarificationSignals()).prompt() == ""


class TestClarifyForbidsChaos:
    def test_any_signal_switches_to_clarify(self):
        out = evaluate(ClarificationSignals(no_target=True))
        assert out.mode == "clarify"
        assert out.should_clarify is True

    def test_clarify_forbids_the_whole_chaos_set(self):
        out = evaluate(ClarificationSignals(loop_suspected=True))
        assert out.forbidden_actions == FORBIDDEN_IN_CLARIFY
        # The exact actions a bad agent would take must all be blocked.
        for action in (
            "spawn_subagent",
            "create_module",
            "write_memory",
            "create_proposal",
            "expensive_llm_call",
        ):
            assert out.is_forbidden(action) is True
