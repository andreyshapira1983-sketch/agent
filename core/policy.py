"""Policy Gate — pre-execution checkpoint for every Action.

MVP rules (Action Risk & Reversibility, §5 of the architecture):
  - read_only           -> allow
  - reversible          -> allow with audit reason
  - irreversible        -> escalate (requires human approval)
  - external            -> escalate
Unknown tool             -> deny
"""
from __future__ import annotations

from typing import Any

from core.models import Action, PolicyDecision
from tools.base import ToolRegistry

POLICY_ID = "mvp-default-policy"


class PolicyGate:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        # Optional run-scoped block-list: tools named here are denied before any risk evaluation (the autonomous runtime forbids file_write, shell_exec in a dry-run). Empty by default.
        self.blocked_tools: frozenset[str] = frozenset()
        # Optional run-scoped escalation list: tools named here have their `reversible` verdict PROMOTED to `escalate`, so even a safe new-file write needs human approval (operator scripts bypass this gate; the agent loop must ask). Empty by default.
        self.escalate_reversible_tools: frozenset[str] = frozenset()
        # Pressure as a number (core/pressure_gate.py): a callable saying whether pressure exists (budget running out) and why; under pressure ANY reversible action waits for a human. None: not measured.
        self.pressure: Any = None
        # Outbound data flow (core/egress_flow.py): the turn's journal of what the human asked and what was read. Set by the loop at turn start; None: not measured.
        self.egress: Any = None

    def _effective_blocked_tools(self) -> frozenset[str]:
        """The host's block set UNION the current run's own.

        A run may forbid more than the host and never less (no subtraction); overwriting `blocked_tools` and restoring it in a `finally` turned into silent widening with two overlapping runs.
        """
        from core.run_context import run_blocked_tools

        return frozenset(self.blocked_tools) | run_blocked_tools()

    def check(self, action: Action) -> PolicyDecision:
        subject = action.tool_name or action.type

        if action.type != "tool_call":
            return PolicyDecision(
                policy_id=POLICY_ID,
                subject=subject,
                action=action.type,
                decision="allow",
                reasons=["non-tool action: internal LLM/output"],
            )

        if action.tool_name is None:
            return PolicyDecision(
                policy_id=POLICY_ID,
                subject="<missing>",
                action="tool_call",
                decision="deny",
                reasons=["tool_call action without tool_name"],
            )

        try:
            tool = self.registry.get(action.tool_name)
        except KeyError:
            return PolicyDecision(
                policy_id=POLICY_ID,
                subject=action.tool_name,
                action="tool_call",
                decision="deny",
                reasons=[f"tool '{action.tool_name}' not in registry"],
            )

        if action.tool_name in self._effective_blocked_tools():
            return PolicyDecision(
                policy_id=POLICY_ID,
                subject=action.tool_name,
                action="tool_call",
                decision="deny",
                reasons=[
                    (f"tool '{action.tool_name}' is blocked in this context "
                    "(effects disabled / dry-run)")
                ],
            )

        # "Read only" at an address taken from private data that was read is an outbound send, not a read (CaMeL: look at where the call's parts came from).
        leak = self.egress.leak(action.tool_name, action.parameters or {}) if self.egress is not None else None
        if leak:
            return PolicyDecision(policy_id=POLICY_ID, subject=tool.name, action="tool_call",
                                  decision="escalate", reasons=[leak])

        # Argument-aware risk: e.g. file_write is `reversible` for a new path, `irreversible` when overwriting; tools without `risk_for` use their static risk.
        effective_risk = tool.risk_for(action.parameters or {})

        if effective_risk == "read_only":
            return PolicyDecision(
                policy_id=POLICY_ID,
                subject=tool.name,
                action="tool_call",
                decision="allow",
                reasons=["read-only tool"],
            )
        if effective_risk == "reversible":
            pressed = self.pressure() if callable(self.pressure) else ""
            if pressed:
                return PolicyDecision(
                    policy_id=POLICY_ID, subject=tool.name, action="tool_call", decision="escalate",
                    reasons=[(f"reversible action ({tool.name}) under budget pressure ({pressed}): "
                              "effects wait for a human until the window refills")],
                )
            if action.tool_name in self.escalate_reversible_tools:
                return PolicyDecision(
                    policy_id=POLICY_ID,
                    subject=tool.name,
                    action="tool_call",
                    decision="escalate",
                    reasons=[
                        (f"reversible action ({tool.name}) requires approval in "
                        "this context (agent/runtime writes need a human checkpoint)")
                    ],
                )
            return PolicyDecision(
                policy_id=POLICY_ID,
                subject=tool.name,
                action="tool_call",
                decision="allow",
                reasons=[f"reversible action ({tool.name}); audit logged"],
            )
        return PolicyDecision(
            policy_id=POLICY_ID,
            subject=tool.name,
            action="tool_call",
            decision="escalate",
            reasons=[f"action risk={effective_risk} requires human approval"],
        )
