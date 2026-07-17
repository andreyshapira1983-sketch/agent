"""Actuation gateway — checked door for effectful actions (REPL, runtime, daemon).

Gateway answers **may it happen?** before effectful tool handlers run. It delegates
risk classification to :class:`core.policy.PolicyGate`, consults kill-switch /
readiness hard stops (G5a), and adds path/dry-run context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.gateway_consult import collect_hard_stop_reasons
from core.models import Action, PolicyDecision
from core.policy import PolicyGate
from tools.base import ToolRegistry

GatewayPath = Literal["repl", "runtime", "daemon", "self_apply", "cli"]
GatewayOutcome = Literal["allow", "deny", "escalate", "simulate", "passthrough", "block"]

# Effectful tools routed through gateway (see gateway-proposal.md).
EFFECTFUL_TOOL_NAMES: frozenset[str] = frozenset({"file_write", "shell_exec"})


def gateway_path_from_receipt(receipt_path: str) -> GatewayPath:
    """Map receipt context path to gateway path (G2 runtime/daemon)."""
    if receipt_path == "daemon":
        return "daemon"
    if receipt_path == "repl":
        return "repl"
    return "runtime"


def is_effectful_tool(
    tool_name: str,
    arguments: dict[str, Any] | None,
    registry: ToolRegistry,
) -> bool:
    """Return True when gateway must evaluate before execution."""
    if tool_name not in EFFECTFUL_TOOL_NAMES:
        return False
    if tool_name == "file_write":
        return True
    if tool_name == "shell_exec":
        try:
            tool = registry.get("shell_exec")
        except KeyError:
            return True
        return tool.risk_for(arguments or {}) != "read_only"
    return False


@dataclass(frozen=True)
class GatewayDecision:
    outcome: GatewayOutcome
    tool_name: str
    path: GatewayPath
    policy: PolicyDecision | None = None
    reasons: tuple[str, ...] = ()

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "tool_name": self.tool_name,
            "path": self.path,
            "policy_decision": (
                self.policy.decision if self.policy is not None else None
            ),
            "reasons": list(self.reasons),
        }


class ActuationGateway:
    """Deterministic facade: policy + dry-run simulate for effectful actions."""

    def __init__(
        self,
        policy: PolicyGate | None = None,
        *,
        path: GatewayPath = "repl",
        dry_run: bool = False,
        kill_switch: Any | None = None,
        budget_snapshot: dict | None = None,
        readiness_blockers: tuple[str, ...] = (),
        check_readiness: bool = False,
    ):
        self.policy = policy
        self.path = path
        self.dry_run = dry_run
        self.kill_switch = kill_switch
        self.budget_snapshot = budget_snapshot
        self.readiness_blockers = readiness_blockers
        self.check_readiness = check_readiness

    def _hard_stop_decision(self, tool_name: str) -> GatewayDecision | None:
        reasons = collect_hard_stop_reasons(
            kill_switch=self.kill_switch,
            budget_snapshot=self.budget_snapshot,
            readiness_blockers=self.readiness_blockers,
            check_readiness=self.check_readiness,
        )
        if not reasons:
            return None
        return GatewayDecision(
            outcome="block",
            tool_name=tool_name,
            path=self.path,
            reasons=reasons,
        )

    def evaluate(
        self,
        action: Action,
        *,
        registry: ToolRegistry,
    ) -> GatewayDecision:
        tool_name = action.tool_name or ""
        args = action.parameters or {}

        if not is_effectful_tool(tool_name, args, registry):
            return GatewayDecision(
                outcome="passthrough",
                tool_name=tool_name,
                path=self.path,
            )

        blocked = self._hard_stop_decision(tool_name)
        if blocked is not None:
            return blocked

        if self.policy is None:
            raise ValueError("ActuationGateway.evaluate requires a PolicyGate")
        decision = self.policy.check(action)
        reasons = tuple(decision.reasons)

        if decision.decision == "deny":
            return GatewayDecision(
                outcome="deny",
                tool_name=tool_name,
                path=self.path,
                policy=decision,
                reasons=reasons,
            )
        if decision.decision == "escalate":
            return GatewayDecision(
                outcome="escalate",
                tool_name=tool_name,
                path=self.path,
                policy=decision,
                reasons=reasons,
            )
        if self.dry_run:
            return GatewayDecision(
                outcome="simulate",
                tool_name=tool_name,
                path=self.path,
                policy=decision,
                reasons=reasons + ("gateway dry_run: effect not executed",),
            )
        return GatewayDecision(
            outcome="allow",
            tool_name=tool_name,
            path=self.path,
            policy=decision,
            reasons=reasons,
        )

    def evaluate_self_apply(
        self,
        *,
        operation: str = "self_apply_lane.run",
    ) -> GatewayDecision:
        """G3: the single actuation door before an approved self-apply lane run.

        Human approval, proposal validation, and low-risk classification happen
        upstream in ``core.self_apply_bridge``. The gateway adds the run-mode
        decision: ``simulate`` under dry-run (no mutation), ``allow`` otherwise.
        Kill-switch / readiness may return ``block`` before any mutation (G5a).
        """
        blocked = self._hard_stop_decision(operation)
        if blocked is not None:
            return blocked
        if self.dry_run:
            return GatewayDecision(
                outcome="simulate",
                tool_name=operation,
                path=self.path,
                reasons=("gateway dry_run: self-apply effect not executed",),
            )
        return GatewayDecision(
            outcome="allow",
            tool_name=operation,
            path=self.path,
        )


def simulate_output(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Structured placeholder when gateway returns simulate (no tool invoke)."""
    return {
        "gateway": "simulate",
        "status": "simulated",
        "tool_name": tool_name,
        "arguments": arguments,
        "message": "effect not executed (actuation gateway simulate)",
    }
