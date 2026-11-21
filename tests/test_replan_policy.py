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
    DEFAULT_BUDGETS,
    FailureBudget,
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


# ============================================================
# ReplanPolicy construction
# ============================================================

class TestReplanPolicyConstruction:
    def test_zero_max_total_rejected(self):
        with pytest.raises(ValueError, match="max_total_replans"):
            ReplanPolicy(max_total_replans=0)

    def test_missing_budget_for_a_type_rejected(self):
        broken = {k: v for k, v in DEFAULT_BUDGETS.items() if k != "tool_error"}
        with pytest.raises(ValueError, match="missing budgets"):
            ReplanPolicy(budgets=broken)


# ============================================================
# ReplanPolicy.decide() — global cap
# ============================================================

class TestGlobalCap:
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


    def test_duplicate_forbidden_action_deduped(self):
        history = [
            FakeTrigger(code="approval_deny", tool_name="t", arguments={"a": 1}),
            FakeTrigger(code="approval_deny", tool_name="t", arguments={"a": 1}),
        ]
        d = self.policy.decide(history, completed_attempts=2)
        # Even though decide() will exhaust here, the forbidden set is
        # populated for the AUDIT log payload regardless.
        assert len(d.forbidden_actions) == 1


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
