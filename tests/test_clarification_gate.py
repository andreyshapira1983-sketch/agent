"""Tests for core.clarification_gate — режим переспроса (ask, don't build)."""
from __future__ import annotations

import pytest

from core.clarification_gate import (
    ALLOWED_IN_CLARIFY,
    FORBIDDEN_IN_CLARIFY,
    ClarificationSignals,
    evaluate,
    for_loop_suspected,
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

    def test_clarify_commits_to_exactly_one_allowed_action(self):
        out = evaluate(ClarificationSignals(goal_too_broad=True))
        # Exactly one human action — never "do everything".
        assert out.allowed_action in ALLOWED_IN_CLARIFY
        assert out.allowed_action == "ask_question"

    def test_allowed_action_is_never_a_chaos_action(self):
        out = evaluate(ClarificationSignals(loop_suspected=True))
        assert out.allowed_action not in FORBIDDEN_IN_CLARIFY


class TestSignalToQuestions:
    def test_loop_suspected_asks_the_human_core_three(self):
        out = for_loop_suspected()
        assert out.mode == "clarify"
        assert out.allowed_action == "ask_question"
        # what / where / success — exactly the three a person would ask.
        joined = " ".join(out.questions)
        assert "построить" in joined
        assert "Где" in joined
        assert "успеха" in joined

    def test_goal_too_broad_asks_what_and_purpose(self):
        out = evaluate(ClarificationSignals(goal_too_broad=True))
        joined = " ".join(out.questions)
        assert "Что именно" in joined
        assert "цели" in joined

    def test_no_success_criterion_asks_for_success(self):
        out = evaluate(ClarificationSignals(no_success_criterion=True))
        assert any("успеха" in q for q in out.questions)

    def test_no_target_asks_what_and_where(self):
        out = evaluate(ClarificationSignals(no_target=True))
        joined = " ".join(out.questions)
        assert "Что именно" in joined
        assert "Где" in joined

    def test_unclear_where_asks_where(self):
        out = evaluate(ClarificationSignals(unclear_where=True))
        assert any("Где" in q for q in out.questions)

    def test_unclear_live_effect_asks_plan_or_do(self):
        out = evaluate(ClarificationSignals(unclear_live_effect=True))
        assert any("live-effect" in q for q in out.questions)

    def test_tied_best_actions_asks_priority(self):
        out = evaluate(ClarificationSignals(tied_best_actions=True))
        assert any("приоритет" in q for q in out.questions)

    def test_subagent_disagreement_asks_source_of_truth(self):
        out = evaluate(ClarificationSignals(subagents_disagree_weak_evidence=True))
        assert any("источник истины" in q for q in out.questions)


class TestQuestionsAreStableAndDeduped:
    def test_overlapping_signals_do_not_duplicate_questions(self):
        # no_target and unclear_where both contribute the "where" question.
        out = evaluate(ClarificationSignals(no_target=True, unclear_where=True))
        where_qs = [q for q in out.questions if "Где" in q]
        assert len(where_qs) == 1

    def test_question_order_is_stable_regardless_of_signal_order(self):
        a = evaluate(ClarificationSignals(no_success_criterion=True, goal_too_broad=True))
        b = evaluate(ClarificationSignals(goal_too_broad=True, no_success_criterion=True))
        assert a.questions == b.questions
        # "what" (broad goal) comes before "success" in the canonical order.
        assert a.questions.index("Что именно нужно построить?") < \
            a.questions.index("Что считать готовым результатом (критерий успеха)?")


class TestReasonAndPrompt:
    def test_loop_suspected_is_the_strongest_reason(self):
        out = evaluate(ClarificationSignals(
            loop_suspected=True,
            unclear_where=True,
        ))
        assert "loop_suspected" in out.reason

    def test_prompt_renders_operator_facing_text(self):
        out = for_loop_suspected()
        text = out.prompt()
        assert text.startswith("Я не могу безопасно продолжить. Уточни:")
        for q in out.questions:
            assert q in text

    def test_to_dict_round_trips_fields(self):
        out = evaluate(ClarificationSignals(loop_suspected=True))
        d = out.to_dict()
        assert d["mode"] == "clarify"
        assert d["allowed_action"] == "ask_question"
        assert d["forbidden_actions"] == list(FORBIDDEN_IN_CLARIFY)
        assert d["questions"] == list(out.questions)
        assert "loop_suspected" in d["triggered_by"]


class TestTriggeredBy:
    def test_triggered_by_lists_only_fired_signals(self):
        out = evaluate(ClarificationSignals(loop_suspected=True, no_target=True))
        assert set(out.triggered_by) == {"loop_suspected", "no_target"}

    @pytest.mark.parametrize("field_name", [
        "goal_too_broad",
        "no_success_criterion",
        "no_target",
        "unclear_where",
        "unclear_live_effect",
        "loop_suspected",
        "tied_best_actions",
        "subagents_disagree_weak_evidence",
    ])
    def test_each_single_signal_triggers_clarify(self, field_name):
        out = evaluate(ClarificationSignals(**{field_name: True}))
        assert out.mode == "clarify"
        assert out.triggered_by == (field_name,)
