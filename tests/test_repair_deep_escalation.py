"""Code repair may reach the deep tier — under a budget, never by itself.

## Why this exists

`core/deep_escalation.py` shipped with `high_value_repair` in `RESERVED_REASONS`
and a comment saying exactly why it was not active:

    Those roles (repair_proposal / verifier / subagent) are wired through
    `for_role` and cannot reach the DEEP tier yet; accepting their reasons now
    would imply behaviour that does not exist.

So the gate was honest and the wiring was missing. The consequence, measured on
a real task (MIR-052, `core/loop_methods2.py`): rewriting 728 lines correctly is
the hardest thing the agent does, and it was sent to whatever model the role
config named — in practice `gpt-4o-mini`, which returned the file unchanged.

## What must stay true

The rule this must NOT break is the one in `deep_escalation`'s own docstring:
*the autonomous agent must never open the most expensive model for itself.*
Wiring repair into the gate is not permission to spend — it is permission to
*ask*, and the answer is still no unless the operator set a budget.

Hence the shape pinned below:

* **off by default** — with no budget configured, behaviour is byte-identical to
  before: the request downgrades and the cheap model is used;
* **complexity is measured, not guessed** — a repair's difficulty comes from the
  file and the failing tests, not from keywords in an operator sentence
  (`assess_complexity` reads prose, and "repair core/loop_methods2.py" contains
  no deep signals at all);
* **the budget is real** — `operator_approved` stays False on the autonomous
  path, so `budget_ok` is the only thing that can unlock deep, and it is
  computed from the ledger rather than asserted.
"""
from __future__ import annotations

from core.deep_escalation import (
    ACTIVE_REASONS,
    ACTIVE_ROLES,
    DeepEscalationRequest,
    evaluate_deep_escalation,
)


def _request(**overrides) -> DeepEscalationRequest:
    base = dict(
        role="repair_proposal",
        reason="high_value_repair",
        expected_output="minimal_patch_plan",
        deep_model_available=True,
        budget_ok=True,
        operator_approved=False,
    )
    base.update(overrides)
    return DeepEscalationRequest(**base)


# ── the gate now knows about repair ──────────────────────────────────────────

def test_repair_is_an_eligible_role():
    assert "repair_proposal" in ACTIVE_ROLES


def test_high_value_repair_is_an_active_reason():
    assert "high_value_repair" in ACTIVE_REASONS


def test_repair_with_a_budget_and_a_concrete_deliverable_is_approved():
    decision = evaluate_deep_escalation(_request())

    assert decision.approved, decision.route_reason
    assert decision.effective_tier == "deep"
    assert "high_value_repair" in decision.route_reason


# ── and every guard still bites ──────────────────────────────────────────────

def test_no_budget_means_no_deep_even_for_repair():
    """The autonomous path cannot approve itself, so budget is the only key."""
    decision = evaluate_deep_escalation(
        _request(budget_ok=False, operator_approved=False)
    )

    assert decision.downgraded
    assert decision.route_reason == "deep_downgraded:budget_block", decision.route_reason


def test_a_vague_deliverable_still_downgrades():
    decision = evaluate_deep_escalation(_request(expected_output="make it better"))

    assert decision.downgraded
    assert "vague_expected_output" in decision.route_reason


def test_being_hard_is_not_a_reason():
    """"complexity" as a reason was always refused — that must not change."""
    decision = evaluate_deep_escalation(_request(reason="complexity"))

    assert decision.downgraded
    assert "missing_reason" in decision.route_reason


def test_an_unwired_role_is_still_refused():
    """Activating repair must not open the gate for verifier/subagent too."""
    for role in ("verifier", "subagent", "memory_summary"):
        decision = evaluate_deep_escalation(_request(role=role))
        assert decision.downgraded, role
        assert "role_not_eligible" in decision.route_reason


def test_no_deep_model_downgrades_before_anything_else():
    decision = evaluate_deep_escalation(_request(deep_model_available=False))

    assert decision.downgraded
    assert "no_deep_model" in decision.route_reason


# ── the budget itself: off unless the operator turns it on ───────────────────

def test_deep_budget_is_zero_by_default(monkeypatch):
    """No env var → no deep calls allowed → today's behaviour, unchanged."""
    from core.deep_escalation import deep_call_budget

    monkeypatch.delenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", raising=False)
    assert deep_call_budget() == 0


def test_the_operator_sets_the_budget(monkeypatch):
    from core.deep_escalation import deep_call_budget

    monkeypatch.setenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", "3")
    assert deep_call_budget() == 3


def test_a_malformed_budget_is_treated_as_zero(monkeypatch):
    """An unreadable limit must fail closed, never open."""
    from core.deep_escalation import deep_call_budget

    for bad in ("", "many", "-2", "3.5"):
        monkeypatch.setenv("AGENT_DEEP_MAX_CALLS_PER_SESSION", bad)
        assert deep_call_budget() == 0, bad


def test_budget_ok_counts_deep_calls_already_made():
    """Spent budget is read from the ledger, not assumed."""
    from core.deep_escalation import deep_budget_ok

    class _Record:
        def __init__(self, route_reason: str) -> None:
            self.route_reason = route_reason

    class _Ledger:
        def __init__(self, reasons: list[str]) -> None:
            self.records = [_Record(r) for r in reasons]

    spent_none = _Ledger(["complexity:standard", "policy:balanced:x"])
    spent_two = _Ledger([
        "deep_approved:high_value_repair",
        "complexity:standard",
        "deep_approved:operator_explicitly_requested_opus",
    ])

    assert deep_budget_ok(spent_none, limit=1) is True
    assert deep_budget_ok(spent_two, limit=3) is True
    assert deep_budget_ok(spent_two, limit=2) is False, "at the limit, not under it"
    assert deep_budget_ok(spent_two, limit=0) is False
    assert deep_budget_ok(None, limit=5) is True, "no ledger → nothing spent yet"
    assert deep_budget_ok(spent_none, limit=0) is False, "zero means off"
