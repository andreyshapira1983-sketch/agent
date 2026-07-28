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
from typing import Any, Literal

EscalationTier = Literal["deep", "standard"]
GateOutcome = Literal["approved", "downgraded"]


# Roles allowed to escalate to deep at all (the only for_task callers).
# `repair_proposal` joined them when `propose_repair` was moved off `for_role`:
# rewriting a whole module correctly is the hardest thing the agent attempts,
# and it was being sent to whatever the role config happened to name.
ACTIVE_ROLES: frozenset[str] = frozenset({
    "planner", "synthesizer", "repair_proposal",
})

# Structured reasons that may unlock deep. "complexity" / "better quality" /
# "just in case" are intentionally absent: the task being hard is not a reason,
# the operator must say WHY the standard tier is not enough.
ACTIVE_REASONS: frozenset[str] = frozenset({
    "operator_explicitly_requested_opus",
    "planner_multi_file_architecture_change",
    # Active since `propose_repair` routes through `for_task`. Note what this
    # does and does not buy: it lets a repair *ask* for the deep tier. The
    # answer is still no unless `deep_budget_ok` says the operator funded it —
    # `operator_approved` stays False on the autonomous path, so the budget is
    # the only key, and it is zero until someone sets it.
    "high_value_repair",
})

# Reserved until verifier/subagent are routed through the same gate. Documented
# here so the boundary is explicit, but NOT accepted: accepting a reason for a
# role that cannot reach deep would imply behaviour that does not exist.
RESERVED_REASONS: frozenset[str] = frozenset({
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
    #: State of the model cache when the tier could not be resolved: `expired`,
    #: `missing`, `unreadable`, or `fresh`/None when the cache is not the
    #: explanation. All three bad states mean "no model at this tier" is a
    #: *cache* fact rather than a provider fact, and all three are fixed by
    #: `:refresh-models` — but they are reported separately because "never built"
    #: and "went stale" are different things to read in a ledger.
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


#: Env var funding autonomous deep calls for this session. **Zero by default**,
#: and that default is the whole safety story: with it unset, an autonomous
#: repair asks for the deep tier, `deep_budget_ok` says no, and the run
#: continues on the standard model exactly as it did before this was wired.
#: Turning it on is an operator act, and it is a *ceiling*, not an allowance.
DEEP_BUDGET_ENV = "AGENT_DEEP_MAX_CALLS_PER_SESSION"

#: Route reasons written by an approved deep call. Counting these is how spend
#: is measured — from the ledger the router already writes, not from a second
#: counter that could drift away from it.
_DEEP_APPROVED_PREFIX = "deep_approved:"


def deep_call_budget() -> int:
    """How many autonomous deep calls the operator has funded this session.

    Fails closed on anything unreadable: an unset, empty, negative, fractional
    or non-numeric limit is zero. A budget nobody can parse must not become a
    budget nobody bounded.
    """
    import os

    raw = (os.getenv(DEEP_BUDGET_ENV) or "").strip()
    if not raw.isdigit():          # rejects "", "-2", "3.5", "many"
        return 0
    return int(raw)


def deep_budget_ok(usage_ledger: Any, *, limit: int) -> bool:
    """Is there room under `limit` for one more deep call?

    Spend is counted from the usage ledger's own records, so the number cannot
    disagree with what was actually billed. `limit <= 0` is off — no ledger
    state can unlock it.
    """
    if limit <= 0:
        return False
    records = getattr(usage_ledger, "records", None) or []
    spent = sum(
        1 for record in records
        if str(getattr(record, "route_reason", "")).startswith(_DEEP_APPROVED_PREFIX)
    )
    return spent < limit


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
        # Same outcome, different instruction. "No model at this tier" tells the
        # operator to look at their provider; a cache state tells them the list
        # is stale/absent and one command fixes it. Reporting the first when the
        # second is true costs an investigation — that is the whole point of
        # carrying the status. `missing` and `unreadable` count too: they were
        # falling through to `no_deep_model`, which is exactly the misleading
        # message this distinction exists to remove.
        status = request.catalog_status
        if status and status != "fresh":
            return _downgrade(f"catalog_{status}")
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
