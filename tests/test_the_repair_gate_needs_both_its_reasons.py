"""The repair gate's two preconditions must each be load-bearing.

Found 2026-08-20 auditing the suite. `GovernancePolicy._repair` denies an
APPLY_CODE_CHANGE unless the diagnosis was verified AND a rollback plan
exists; with both, it still only escalates to human approval. Removing
`evidence_verified` from that condition — so an unverified diagnosis reaches
the approval queue instead of being refused — left 257 tests green across all
twelve files that mention governance.

The reason nothing noticed: the only `evidence_verified=False` case in the
suite is about LEARNING mode and WRITE_MEMORY. Repair mode's deny branch was
exercised only with both preconditions true, which cannot show that either one
matters.

Scope, stated so it is not read as more: this is a governance weakening, not a
hole in autonomous writing. A human still approves in the broken world. What
was missing is the witness that the gate's refusal depends on the diagnosis
being verified at all.
"""
from __future__ import annotations

from core.governance import AgentMode, GovernancePolicy, GovernedOperation


def _repair_write(*, verified: bool, rollback: bool):
    return GovernancePolicy().evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.APPLY_CODE_CHANGE,
        evidence_verified=verified,
        has_rollback=rollback,
    )


def test_an_unverified_diagnosis_is_denied_not_queued() -> None:
    decision = _repair_write(verified=False, rollback=True)
    assert decision.denied, (
        "a repair write on an unverified diagnosis was not denied — it reached "
        f"{decision.decision!r}, which puts the question to a human instead of "
        "refusing it"
    )


def test_a_missing_rollback_plan_is_denied() -> None:
    assert _repair_write(verified=True, rollback=False).denied


def test_both_reasons_together_still_only_reach_approval() -> None:
    """Boundary pin: satisfying the gate must not become permission. If this
    ever reads `allow`, the lane writes code without a human."""
    decision = _repair_write(verified=True, rollback=True)
    assert decision.requires_approval
    assert not decision.denied


def test_a_diff_proposal_needs_a_verified_diagnosis_to_be_allowed() -> None:
    policy = GovernancePolicy()
    unverified = policy.evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.PROPOSE_DIFF,
        evidence_verified=False,
    )
    verified = policy.evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.PROPOSE_DIFF,
        evidence_verified=True,
    )
    assert unverified.requires_approval
    assert not verified.requires_approval and not verified.denied
