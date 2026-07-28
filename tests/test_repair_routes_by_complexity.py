"""Repair picks its model by the job's size, not by a fixed config line.

`propose_repair` built its model with `model_router.for_role(REPAIR_PROPOSAL)` —
one line, and the reason the hardest work in the system ran on whatever the role
config named. Measured: `gpt-4o-mini` was handed a 728-line rewrite and returned
the file unchanged.

`for_role` cannot escalate. `for_task` can, and it routes the request through the
same operator gate everything else uses, so wiring repair into it does not widen
the agent's authority — it only lets repair *ask*.

## Why complexity is measured here, not read from the prompt

`assess_complexity` scans prose for signals like "архитектур", "audit", "from
scratch". A repair request reads `repair core/loop_methods2.py so the failing
tests pass` — no signal, so it would always assess STANDARD no matter how large
the file. Difficulty for a repair is an objective property of the work: how much
code must be reproduced, and how many tests are red. That is computed and passed
as `force_tier`, which the router still runs through the escalation gate (its
docstring: "a forced DEEP still passes through the operator-escalation check").
"""
from __future__ import annotations

from pathlib import Path

from core.task_complexity import ComplexityTier


def _tier(**kwargs) -> ComplexityTier:
    from core.repair_proposal import repair_complexity

    return repair_complexity(**kwargs)


# ── the size of the job decides ──────────────────────────────────────────────

def test_a_small_file_is_ordinary_work():
    assert _tier(target_chars=1_500, failing_tests=1) is ComplexityTier.STANDARD


def test_a_large_rewrite_is_deep_work():
    """The MIR-052 case: 728 lines to reproduce exactly, with an edit inside."""
    assert _tier(target_chars=36_959, failing_tests=2) is ComplexityTier.DEEP


def test_many_failing_tests_make_a_mid_sized_file_deep():
    """Several red tests at once means the change is not a one-liner."""
    assert _tier(target_chars=9_000, failing_tests=1) is ComplexityTier.STANDARD
    assert _tier(target_chars=9_000, failing_tests=5) is ComplexityTier.DEEP


def test_a_trivial_target_never_asks_for_the_expensive_model():
    for chars in (0, 200, 900):
        assert _tier(target_chars=chars, failing_tests=1) is not ComplexityTier.DEEP


# ── and the gate still decides whether asking gets anything ──────────────────

def test_deep_work_downgrades_when_the_operator_funded_nothing(monkeypatch, tmp_path):
    """Off by default: the assessment says DEEP, the budget says no, cheap wins.

    This is the property that makes the whole change safe to merge — with no
    env var set, an autonomous repair behaves exactly as it did before.
    """
    monkeypatch.delenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", raising=False)

    from core.deep_escalation import (
        DeepEscalationRequest,
        deep_budget_ok,
        deep_call_budget,
        evaluate_deep_escalation,
    )

    limit = deep_call_budget()
    decision = evaluate_deep_escalation(DeepEscalationRequest(
        role="repair_proposal",
        reason="high_value_repair",
        expected_output="minimal_patch_plan",
        deep_model_available=True,
        budget_ok=deep_budget_ok(None, limit=limit),
        operator_approved=False,
    ))

    assert limit == 0
    assert decision.downgraded
    assert "budget_block" in decision.route_reason


def test_deep_work_is_allowed_once_the_operator_funds_it(monkeypatch):
    monkeypatch.setenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", "2")

    from core.deep_escalation import (
        DeepEscalationRequest,
        deep_budget_ok,
        deep_call_budget,
        evaluate_deep_escalation,
    )

    decision = evaluate_deep_escalation(DeepEscalationRequest(
        role="repair_proposal",
        reason="high_value_repair",
        expected_output="minimal_patch_plan",
        deep_model_available=True,
        budget_ok=deep_budget_ok(None, limit=deep_call_budget()),
        operator_approved=False,
    ))

    assert decision.approved, decision.route_reason


# ── the wiring itself ────────────────────────────────────────────────────────

def test_propose_repair_no_longer_uses_the_unescalatable_path():
    """`for_role` cannot reach deep at all — repair must not be built on it."""
    source = Path("core/loop_methods.py").read_text(encoding="utf-8")
    start = source.index("def propose_repair(")
    end = source.index("def repair(", start)
    body = source[start:end]

    # The call, not the word: the docstring explains why `for_role` was wrong,
    # and a substring check would trip over its own explanation.
    assert "model_router.for_role(" not in body, (
        "propose_repair still builds its model through for_role, which cannot "
        "escalate whatever the task turns out to be"
    )
    assert "model_router.for_task(" in body


def test_propose_repair_asks_with_a_concrete_deliverable():
    """A vague ask downgrades by design, so the wiring must name what it wants."""
    source = Path("core/loop_methods.py").read_text(encoding="utf-8")
    start = source.index("def propose_repair(")
    end = source.index("def repair(", start)
    body = source[start:end]

    assert "high_value_repair" in body
    assert "minimal_patch_plan" in body
    assert "operator_approved=False" in body, (
        "the autonomous path must not mark itself operator-approved — that "
        "would bypass the budget check entirely"
    )
