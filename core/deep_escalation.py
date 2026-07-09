"""Deep/Opus escalation gate — "Opus is an event, not a habit".

A pure, deterministic decision layer that governs the single expensive seam in
the runtime: ``ModelRouter.for_task`` escalating to the DEEP tier (opus / o-series).
The autonomous agent must NEVER open the most expensive model *for itself*.
Deep escalation is unlocked only by an explicit, structured operator reason
plus a concrete expected output. With no valid reason the request *gracefully
downgrades* to the standard tier — the task still runs, just on the cheaper
brain — instead of failing. There is no I/O, no LLM call, and no free-text
judgement here: free text can never decide whether to spend money on a model.

The decision is recorded ONLY as a ``route_reason`` string (ledger variant A),
so ``data/model_usage.jsonl`` keeps its existing schema and integrity hashes.

v1 scope (verified against the codebase, 2026-06-07)
────────────────────────────────────────────────────
* Applies only to the ``for_task`` path (``core/loop.py`` ``run()`` →
  ``core/model_router.py`` ``for_task``). That path is reached both from the
  interactive ``--ask`` answer cycle AND from an autonomous campaign goal task
  (``core/autonomous_runtime.py`` → ``self.agent.run(...)``). The default
  (no reason supplied) downgrade therefore protects the autonomous path
  automatically: the agent cannot self-open Opus.
* Active roles: ``planner``, ``synthesizer`` (the only ``for_task`` callers).
* Active reasons: ``operator_explicitly_requested_opus``,
  ``planner_multi_file_architecture_change``.
* Reserved for v2 (NOT active): ``high_value_repair``,
  ``critical_inconclusive_verification``, ``subagent_disagreement_high_risk``.
  Those roles (repair_proposal / verifier / subagent) are wired through
  ``for_role`` and cannot reach the DEEP tier yet; accepting their reasons now
  would imply behaviour that does not exist, so they are deliberately kept out
  of ``ACTIVE_REASONS``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EscalationTier = Literal["deep", "standard"]
GateOutcome = Literal["approved", "downgraded"]


# Roles allowed to escalate to deep at all (the only for_task callers).
ACTIVE_ROLES: frozenset[str] = frozenset({"planner", "synthesizer"})

# Structured reasons that may unlock deep. "complexity" / "better quality" /
# "just in case" are intentionally absent: the task being hard is not a reason,
# the operator must say WHY the standard tier is not enough.
ACTIVE_REASONS: frozenset[str] = frozenset({
    "operator_explicitly_requested_opus",
    "planner_multi_file_architecture_change",
})

# Reserved for a future v2 once repair/verifier/subagent are routed through the
# same gate. Documented here so the boundary is explicit, but NOT accepted.
RESERVED_REASONS: frozenset[str] = frozenset({
    "high_value_repair",
    "critical_inconclusive_verification",
    "subagent_disagreement_high_risk",
})

# Concrete deliverables a deep call may be asked for. A vague expected output
# ("make it better") is not in the set and therefore downgrades.
EXPECTED_OUTPUTS: frozenset[str] = frozenset({
    "minimal_patch_plan",
    "architecture_tradeoff",
    "cross_file_synthesis",
    "final_answer_high_stakes",
})


@dataclass(frozen=True)
class OperatorEscalation:
    """Role-free escalation context supplied by the operator at the CLI.

    The router attaches the concrete role when it evaluates the gate, so this
    object only carries the operator's structured intent. ``operator_approved``
    and ``budget_ok`` default to ``True`` because a human typing ``--reason`` at
    the interactive ``--ask`` prompt IS present and approving; the autonomous
    path never constructs this object and so always falls through to a downgrade.
    """

    reason: str | None = None
    expected_output: str | None = None
    budget_ok: bool = True
    operator_approved: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "expected_output": self.expected_output,
            "budget_ok": self.budget_ok,
            "operator_approved": self.operator_approved,
        }


@dataclass(frozen=True)
class DeepEscalationRequest:
    """A single role-specific request to escalate to the deep tier."""

    role: str
    reason: str | None = None
    expected_output: str | None = None
    deep_model_available: bool = True
    budget_ok: bool = False
    operator_approved: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "reason": self.reason,
            "expected_output": self.expected_output,
            "deep_model_available": self.deep_model_available,
            "budget_ok": self.budget_ok,
            "operator_approved": self.operator_approved,
        }


@dataclass(frozen=True)
class DeepEscalationDecision:
    """The gate's verdict: which tier to actually use, and why."""

    effective_tier: EscalationTier
    gate: GateOutcome
    route_reason: str

    @property
    def approved(self) -> bool:
        return self.gate == "approved"

    @property
    def downgraded(self) -> bool:
        return self.gate == "downgraded"

    def to_dict(self) -> dict[str, object]:
        return {
            "effective_tier": self.effective_tier,
            "gate": self.gate,
            "route_reason": self.route_reason,
        }


def _downgrade(code: str) -> DeepEscalationDecision:
    return DeepEscalationDecision(
        effective_tier="standard",
        gate="downgraded",
        route_reason=f"deep_downgraded:{code}",
    )


def evaluate_deep_escalation(request: DeepEscalationRequest) -> DeepEscalationDecision:
    """Decide whether a deep-tier request is allowed or must downgrade.

    Pure and deterministic. Called only for DEEP-tier requests (LIGHT/cheap
    escalation is never gated). Returns a downgrade decision for every failing
    check rather than raising, so the caller can always continue on the
    standard tier and the task never fails just because Opus is not allowed.
    """
    if request.role not in ACTIVE_ROLES:
        return _downgrade("role_not_eligible")
    if not request.deep_model_available:
        return _downgrade("no_deep_model")
    if request.reason not in ACTIVE_REASONS:
        return _downgrade("missing_reason")
    if request.expected_output not in EXPECTED_OUTPUTS:
        return _downgrade("vague_expected_output")
    if not (request.budget_ok or request.operator_approved):
        return _downgrade("budget_block")
    return DeepEscalationDecision(
        effective_tier="deep",
        gate="approved",
        route_reason=f"deep_approved:{request.reason}",
    )
