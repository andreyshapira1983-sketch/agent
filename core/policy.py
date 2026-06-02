"""Policy Gate — pre-execution checkpoint for every Action.

MVP rules (Action Risk & Reversibility, §5 of the architecture):
  - read_only           -> allow
  - reversible          -> allow with audit reason
  - irreversible        -> escalate (requires human approval)
  - external            -> escalate
Unknown tool             -> deny
"""
from __future__ import annotations

from core.models import Action, PolicyDecision
from tools.base import ToolRegistry


POLICY_ID = "mvp-default-policy"


class PolicyGate:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

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

        # Argument-aware risk: e.g. file_write is `reversible` when the
        # target is a new path but `irreversible` when it would overwrite.
        # Tools that don't override risk_for fall back to their static risk.
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
