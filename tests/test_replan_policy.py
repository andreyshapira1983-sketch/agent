"""MVP-12 unit tests: FailureBudget + ReplanPolicy.decide().

Pure-function tests on the policy itself. No AgentLoop, no logging, no
LLM. Acceptance tests for the full loop live in
`test_replan_policy_integration.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.replan import (
    ALL_FAILURE_TYPES,
    DEFAULT_BUDGETS,
    DEFAULT_MAX_TOTAL_REPLANS,
    FailureBudget,
    FailureType,
    ReplanDecision,
    ReplanPolicy,
)


@dataclass
class FakeTrigger:
    """Minimal stand-in for `core.loop.ReplanTrigger` so this test file
    doesn't import the loop. Has the same duck-typed surface decide()
    reads: `code`, `tool_name`, `arguments`.
    """

    code: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.arguments is None:
            self.arguments = {}


# ============================================================
# FailureBudget validation
# ============================================================

class TestFailureBudget:
    def test_zero_max_occurrences_rejected(self):
        with pytest.raises(ValueError, match="max_occurrences"):
            FailureBudget(max_occurrences=0, advice="x")

    def test_negative_max_occurrences_rejected(self):
        with pytest.raises(ValueError, match="max_occurrences"):
            FailureBudget(max_occurrences=-1, advice="x")

    def test_minimum_valid_max_occurrences_is_one(self):
        b = FailureBudget(max_occurrences=1, advice="x")
        assert b.max_occurrences == 1
        assert b.requires_different_action is False

    def test_requires_different_action_default_false(self):
        assert DEFAULT_BUDGETS["tool_error"].requires_different_action is False
        assert DEFAULT_BUDGETS["verify_failed"].requires_different_action is False
        assert DEFAULT_BUDGETS["web_empty"].requires_different_action is False
        assert DEFAULT_BUDGETS["timeout"].requires_different_action is False

    def test_requires_different_action_true_for_approval_and_policy_failures(self):
        for code in ("approval_deny", "approval_abort", "approval_unavailable", "policy_blocked"):
            assert DEFAULT_BUDGETS[code].requires_different_action is True


# ============================================================
# DEFAULT_BUDGETS coverage
# ============================================================

class TestDefaultBudgetsCoverage:
    def test_every_failure_type_has_a_budget(self):
        """Bug bait: a new FailureType added without a budget would
        crash decide() at runtime. ReplanPolicy.__post_init__ catches
        this at construction time."""
        missing = set(ALL_FAILURE_TYPES) - set(DEFAULT_BUDGETS.keys())
        assert missing == set(), f"DEFAULT_BUDGETS is missing: {missing}"

    def test_all_default_budgets_are_at_least_one(self):
        for code, budget in DEFAULT_BUDGETS.items():
            assert budget.max_occurrences >= 1, (
                f"{code} budget {budget.max_occurrences} < 1"
            )

    def test_recoverable_failures_have_room_for_retries(self):
        # Recoverable categories should allow at least one retry.
        for code in ("tool_error", "verify_failed", "web_empty", "timeout"):
            assert DEFAULT_BUDGETS[code].max_occurrences >= 2

    def test_advice_strings_are_non_empty(self):
        for code, budget in DEFAULT_BUDGETS.items():
            assert budget.advice.strip() != ""


# ============================================================
# ReplanPolicy construction
# ============================================================

class TestReplanPolicyConstruction:
    def test_default_policy_builds(self):
        p = ReplanPolicy()
        assert p.max_total_replans == DEFAULT_MAX_TOTAL_REPLANS

    def test_zero_max_total_rejected(self):
        with pytest.raises(ValueError, match="max_total_replans"):
            ReplanPolicy(max_total_replans=0)

    def test_missing_budget_for_a_type_rejected(self):
        broken = {k: v for k, v in DEFAULT_BUDGETS.items() if k != "tool_error"}
        with pytest.raises(ValueError, match="missing budgets"):
            ReplanPolicy(budgets=broken)

    def test_custom_budget_replaces_default(self):
        custom = dict(DEFAULT_BUDGETS)
        custom["tool_error"] = FailureBudget(max_occurrences=5, advice="x")
        p = ReplanPolicy(budgets=custom, max_total_replans=10)
        assert p.budgets["tool_error"].max_occurrences == 5


# ============================================================
# ReplanPolicy.decide() — per-FailureType behaviour
# ============================================================

class TestDecideCoreCases:
    def setup_method(self):
        # 10-replan cap so we can hit per-type budgets cleanly.
        self.policy = ReplanPolicy(max_total_replans=10)

    def test_empty_history_continues(self):
        d = self.policy.decide([], completed_attempts=1)
        assert d.action == "continue"
        assert d.advice_for_planner == ""
        assert d.failure_counts == {}
        assert d.forbidden_actions == ()

    def test_one_tool_error_continues_with_advice(self):
        d = self.policy.decide([FakeTrigger(code="tool_error")], completed_attempts=1)
        assert d.action == "continue"
        assert "different" in d.advice_for_planner.lower()
        assert d.failure_counts == {"tool_error": 1}

    def test_two_tool_errors_exhausts_budget(self):
        """tool_error budget is 2 → 2 occurrences must stop."""
        history = [FakeTrigger(code="tool_error"), FakeTrigger(code="tool_error")]
        d = self.policy.decide(history, completed_attempts=2)
        assert d.action == "abort_no_retry"
        assert "tool_error" in d.reason
        assert "2/2" in d.reason

    def test_two_web_empty_exhausts(self):
        history = [FakeTrigger(code="web_empty"), FakeTrigger(code="web_empty")]
        d = self.policy.decide(history, completed_attempts=2)
        assert d.action == "abort_no_retry"
        assert "web_empty" in d.reason

    def test_two_timeout_exhausts(self):
        history = [FakeTrigger(code="timeout"), FakeTrigger(code="timeout")]
        d = self.policy.decide(history, completed_attempts=2)
        assert d.action == "abort_no_retry"
        assert "timeout" in d.reason

    def test_one_approval_deny_still_allows_retry_if_action_different(self):
        """approval_deny has max=2 + requires_different_action=True. After
        the first deny we should be allowed to try ONCE more, but the
        forbidden_actions list must contain the rejected pair."""
        history = [FakeTrigger(
            code="approval_deny",
            tool_name="danger",
            arguments={"path": "x"},
        )]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.action == "continue"
        assert len(d.forbidden_actions) == 1
        assert d.forbidden_actions[0][0] == "danger"
        assert '"path"' in d.forbidden_actions[0][1]

    def test_two_approval_denies_exhausts(self):
        history = [
            FakeTrigger(code="approval_deny", tool_name="t", arguments={}),
            FakeTrigger(code="approval_deny", tool_name="t", arguments={}),
        ]
        d = self.policy.decide(history, completed_attempts=2)
        assert d.action == "abort_no_retry"
        assert "approval_deny" in d.reason

    def test_approval_unavailable_exhausts_immediately(self):
        """No retry value when the channel is missing."""
        history = [FakeTrigger(
            code="approval_unavailable", tool_name="t", arguments={}
        )]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.action == "abort_no_retry"
        assert "approval_unavailable" in d.reason

    def test_policy_blocked_allows_one_alternative(self):
        history = [FakeTrigger(
            code="policy_blocked", tool_name="bad_tool", arguments={"x": 1}
        )]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.action == "continue"
        # The blocked action enters forbidden_actions.
        assert d.forbidden_actions == (("bad_tool", '{"x": 1}'),)

    def test_unknown_failure_exhausts_immediately(self):
        history = [FakeTrigger(code="unknown")]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.action == "abort_no_retry"


# ============================================================
# ReplanPolicy.decide() — global cap
# ============================================================

class TestGlobalCap:
    def test_global_cap_stops_before_per_type_budgets(self):
        """Global cap is independent: even mixed never-exhausted budgets
        must stop at max_total_replans."""
        policy = ReplanPolicy(max_total_replans=2)
        # Mix one tool_error + one verify_failed — neither budget bit.
        history = [
            FakeTrigger(code="tool_error"),
            FakeTrigger(code="verify_failed"),
        ]
        d = policy.decide(history, completed_attempts=2)
        assert d.action == "abort_exhausted"
        assert "global replan cap" in d.reason

    def test_completed_zero_attempts_raises(self):
        policy = ReplanPolicy()
        with pytest.raises(ValueError, match=">= 1"):
            policy.decide([], completed_attempts=0)


# ============================================================
# Forbidden-action canonicalisation
# ============================================================

class TestForbiddenActions:
    def setup_method(self):
        self.policy = ReplanPolicy(max_total_replans=10)

    def test_args_serialised_with_sorted_keys(self):
        """Order of dict keys must not matter — the planner sanitiser
        compares against this canonical form."""
        history = [FakeTrigger(
            code="approval_deny",
            tool_name="t",
            arguments={"b": 1, "a": 2},
        )]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.forbidden_actions == (("t", '{"a": 2, "b": 1}'),)

    def test_duplicate_forbidden_action_deduped(self):
        history = [
            FakeTrigger(code="approval_deny", tool_name="t", arguments={"a": 1}),
            FakeTrigger(code="approval_deny", tool_name="t", arguments={"a": 1}),
        ]
        d = self.policy.decide(history, completed_attempts=2)
        # Even though decide() will exhaust here, the forbidden set is
        # populated for the AUDIT log payload regardless.
        assert len(d.forbidden_actions) == 1

    def test_recoverable_failures_do_not_populate_forbidden(self):
        history = [FakeTrigger(
            code="tool_error",
            tool_name="t",
            arguments={"x": 1},
        )]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.forbidden_actions == ()

    def test_unjsonable_arguments_dropped_silently(self):
        """A trigger whose arguments aren't JSON-serialisable shouldn't
        crash the policy — it just gets skipped from the forbidden list.
        The advice text still warns the planner."""
        history = [FakeTrigger(
            code="approval_deny",
            tool_name="t",
            arguments={"f": lambda: None},  # type: ignore[dict-item]
        )]
        d = self.policy.decide(history, completed_attempts=1)
        assert d.forbidden_actions == ()
        # Decision still computed normally.
        assert d.action == "continue"


# ============================================================
# ReplanDecision serialisation
# ============================================================

class TestReplanDecisionPayload:
    def test_to_log_payload_shape(self):
        d = ReplanDecision(
            action="continue",
            reason="ok",
            advice_for_planner="line1\nline2",
            failure_counts={"tool_error": 1},
            forbidden_actions=(("t", '{"a":1}'),),
        )
        p = d.to_log_payload()
        assert p == {
            "action": "continue",
            "reason": "ok",
            "advice_chars": len("line1\nline2"),
            "failure_counts": {"tool_error": 1},
            "forbidden_action_count": 1,
        }


# ============================================================
# Coerce: garbage `code` falls back to 'unknown'
# ============================================================

class TestCoerceCode:
    def test_typo_code_treated_as_unknown(self):
        policy = ReplanPolicy()
        history = [FakeTrigger(code="not_a_real_code")]
        # Unknown budget is max_occurrences=1 → first occurrence exhausts.
        d = policy.decide(history, completed_attempts=1)
        assert d.action == "abort_no_retry"
        assert d.failure_counts == {"unknown": 1}

    def test_missing_code_attribute_treated_as_unknown(self):
        class Anon:
            pass

        policy = ReplanPolicy()
        d = policy.decide([Anon()], completed_attempts=1)
        assert d.action == "abort_no_retry"
        assert d.failure_counts == {"unknown": 1}
