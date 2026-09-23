"""Actuation gateway — checked door for effectful actions (REPL, runtime,
daemon).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from core.gateway_consult import (
    APPROVAL_BLOCKER_PREFIX,
    collect_hard_stop_reasons,
)
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
        pending_approval_paths: frozenset[str] | None = None,
    ):
        self.policy = policy
        self.path = path
        self.dry_run = dry_run
        self.kill_switch = kill_switch
        self.budget_snapshot = budget_snapshot
        self.readiness_blockers = readiness_blockers
        self.check_readiness = check_readiness
        #: Файлы висящих заявок. `None` — сведений нет, запрет остаётся общим.
        self.pending_approval_paths = pending_approval_paths

    def _touches_pending_approval(self, args: Mapping[str, Any]) -> bool:
        """Касается ли действие файла, о котором уже лежит заявка."""
        if not self.pending_approval_paths:
            return True
        named: list[str] = []
        for key in ("path", "file_path", "target", "relpath"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                named.append(value)
        for key in ("paths", "files", "file_paths"):
            for value in args.get(key) or ():
                if isinstance(value, str) and value.strip():
                    named.append(value)
        if not named:
            return True  # действие без названного файла судится по-старому
        cleaned = {p.replace("\\", "/").strip().lstrip("./") for p in named}
        return any(p.endswith(tuple(cleaned)) or p in cleaned
                   for p in self.pending_approval_paths)

    def _hard_stop_decision(
        self, tool_name: str, args: Mapping[str, Any] | None = None,
    ) -> GatewayDecision | None:
        reasons = collect_hard_stop_reasons(
            kill_switch=self.kill_switch,
            budget_snapshot=self.budget_snapshot,
            blockers=self.readiness_blockers,
            check_readiness=self.check_readiness,
        )
        # Соразмерность (слово оператора 2026-09-23): висящая заявка запрещает
        # работу с ТЕМИ файлами, которых касается, а не со всеми сразу. Замер:
        # одна заявка про `core/loop_step_execution.py` стоила десяти пустых
        # циклов — конспект по физике в `data/notes` не ложился из-за неё.
        # Остальные жёсткие причины (выключатель, бюджет) не трогаются.
        if reasons and not self._touches_pending_approval(args or {}):
            reasons = tuple(r for r in reasons
                            if APPROVAL_BLOCKER_PREFIX not in r)
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

        blocked = self._hard_stop_decision(tool_name, args)
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
                reasons=(*reasons, "gateway dry_run: effect not executed"),
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
        """G3: the single actuation door before an approved self-apply lane
        run.
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
