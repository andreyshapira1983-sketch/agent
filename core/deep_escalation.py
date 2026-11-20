"""Deep/Opus escalation gate: a pure, deterministic decision on whether ``ModelRouter.for_task`` may use the DEEP tier.

The autonomous agent never opens the most expensive model for itself: escalation needs a structured operator reason plus a concrete
expected output, otherwise the request downgrades to the standard tier. No I/O, no LLM, no free-text judgement; the decision is
recorded only as a ``route_reason`` string so ``data/model_usage.jsonl`` keeps its schema.

Active roles: planner, synthesizer, repair_proposal. Reserved reasons (not accepted) belong to roles wired through ``for_role``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EscalationTier = Literal["deep", "standard"]
GateOutcome = Literal["approved", "downgraded"]


# Roles allowed to escalate to deep at all (the only for_task callers).
ACTIVE_ROLES: frozenset[str] = frozenset({
    "planner", "synthesizer", "repair_proposal",
})

# Structured reasons that may unlock deep; vague ones ("complexity", "just in case") are absent on purpose.
ACTIVE_REASONS: frozenset[str] = frozenset({
    "operator_explicitly_requested_opus",
    "planner_multi_file_architecture_change",
    # a repair may ask for deep, but `deep_budget_ok` decides; the budget is zero until the operator sets it
    "high_value_repair",
})

# Reserved until verifier/subagent are routed through this gate; documented but NOT accepted.
RESERVED_REASONS: frozenset[str] = frozenset({
    "critical_inconclusive_verification",
    "subagent_disagreement_high_risk",
})

# Concrete deliverables a deep call may be asked for; a vague expected output downgrades.
EXPECTED_OUTPUTS: frozenset[str] = frozenset({
    "minimal_patch_plan",
    "architecture_tradeoff",
    "cross_file_synthesis",
    "final_answer_high_stakes",
})


@dataclass(frozen=True)
class OperatorEscalation:
    """Role-free escalation context supplied by the operator at the CLI.

    `operator_approved` and `budget_ok` default to True because a human typing `--reason` is present; the autonomous path never builds this.
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
    #: Model cache state when the tier could not be resolved: `expired`, `missing`, `unreadable`, or `fresh`/None; the first three are fixed by `:refresh-models`.
    catalog_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "reason": self.reason,
            "expected_output": self.expected_output,
            "deep_model_available": self.deep_model_available,
            "budget_ok": self.budget_ok,
            "operator_approved": self.operator_approved,
            "catalog_status": self.catalog_status,
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


#: Env var funding autonomous deep calls per session: zero by default (the safety default), and a ceiling, not an allowance.
DEEP_BUDGET_ENV = "AGENT_DEEP_MAX_CALLS_PER_SESSION"

#: Route reasons written by an approved deep call; spend is counted from the ledger, not a second counter.
_DEEP_APPROVED_PREFIX = "deep_approved:"


def deep_call_budget() -> int:
    """How many autonomous deep calls the operator has funded this session; fails closed (any unparseable value is zero)."""
    import os

    raw = (os.getenv(DEEP_BUDGET_ENV) or "").strip()
    if not raw.isdigit():          # rejects "", "-2", "3.5", "many"
        return 0
    return int(raw)


def deep_budget_ok(usage_ledger: Any, *, limit: int) -> bool:
    """Is there room under `limit` for one more deep call?

    Counts approved deep records with positive token usage; an errored call with tokens counts, a rejected one does not. `limit <= 0` is off.
    """
    if limit <= 0:
        return False
    records = getattr(usage_ledger, "records", None) or []
    spent = sum(
        1
        for record in records
        if (
            str(getattr(record, "route_reason", "")).startswith(
                _DEEP_APPROVED_PREFIX
            )
            and (getattr(record, "total_tokens", 0) or 0) > 0
        )
    )
    return spent < limit


def evaluate_deep_escalation(request: DeepEscalationRequest) -> DeepEscalationDecision:
    """Decide whether a deep-tier request is allowed or must downgrade; pure, never raises.

    Called only for DEEP-tier requests; every failing check returns a downgrade so the task continues on the standard tier.
    """
    if request.role not in ACTIVE_ROLES:
        return _downgrade("role_not_eligible")
    if not request.deep_model_available:
        # same outcome, different instruction: a cache state (expired/missing/unreadable) points to `:refresh-models`, not to the provider
        status = request.catalog_status
        if status and status != "fresh":
            return _downgrade(f"catalog_{status}")
        return _downgrade("no_deep_model")
    if request.reason not in ACTIVE_REASONS:
        # `not_requested` (nobody asked for deep) vs `unknown_reason` (a reason the gate does not know) need different operator reads
        if not (request.reason or "").strip():
            return _downgrade("not_requested")
        return _downgrade("unknown_reason")
    if request.expected_output not in EXPECTED_OUTPUTS:
        return _downgrade("vague_expected_output")
    if not (request.budget_ok or request.operator_approved):
        return _downgrade("budget_block")
    return DeepEscalationDecision(
        effective_tier="deep",
        gate="approved",
        route_reason=f"deep_approved:{request.reason}",
    )
