"""
brain/interpreter.py — LLM Output Interpreter

The Brain interprets what the LLM returned.
Raw LLM text is NEVER passed out of the Brain directly.
The interpreter converts it into a structured ThinkResult.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

EXPECTED_ACTIONS = {"respond", "tool_call", "wait", "stop", "clarify"}


class Interpreter:
    """
    Parses and validates LLM output.
    If LLM returns garbage — Interpreter catches it here.
    The Brain never passes raw LLM text upstream.
    """

    def interpret(self, llm_output: dict, _context: dict) -> "ThinkResult":
        from .core import ThinkResult  # avoid circular import

        try:
            action = self._extract_action(llm_output)
            content = self._extract_content(llm_output)
            confidence = self._extract_confidence(llm_output)
            reasoning = self._extract_reasoning(llm_output)

            if action == "tool_call":
                content, action = self._normalise_tool_call(llm_output, content, action)

            needs_approval = self._check_needs_approval(action, content)

            # Guard: respond/clarify with no content is a silent failure.
            # Downgrade to clarify so Brain explicitly asks user for more info.
            if action in {"respond", "clarify"} and content is None:
                logger.warning(
                    "[Interpreter] action='%s' but content=None — downgrading to 'clarify'",
                    action,
                )
                action = "clarify"
                content = "I need a moment to think. Could you provide more context?"
                confidence = min(confidence, 0.5)

            return ThinkResult(
                action=action,
                content=content,
                confidence=confidence,
                reasoning=reasoning,
                needs_human_approval=needs_approval,
            )

        except (KeyError, TypeError, ValueError) as exc:
            logger.error("[Interpreter] Failed to parse LLM output: %s", exc)
            return ThinkResult(
                action="wait",
                content=None,
                confidence=0.0,
                reasoning=f"Interpreter error: {exc}",
                needs_human_approval=True,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_action(self, output: dict) -> str:
        action = output.get("action", "respond")
        if action not in EXPECTED_ACTIONS:
            logger.warning(
                "[Interpreter] Unknown action '%s' — defaulting to 'wait'", action
            )
            return "wait"
        return action

    def _extract_content(self, output: dict) -> Any:
        return output.get("content") or output.get("text") or output.get("message")

    def _extract_confidence(self, output: dict) -> float:
        raw = output.get("confidence", 0.8)
        try:
            value = float(raw)
            return max(0.0, min(1.0, value))  # clamp to [0, 1]
        except (TypeError, ValueError):
            return 0.5

    def _extract_reasoning(self, output: dict) -> str:
        return output.get("reasoning") or output.get("reason") or "No reasoning provided"

    def _normalise_tool_call(
        self,
        output: dict,
        content: Any,
        action: str,
    ) -> tuple[Any, str]:
        """Force every tool_call into the canonical shape `{tool_name, params}`.

        Different LLM personas have emitted at least three variants:

            * {"tool_name": "x", "params": {...}}     ← documented form
            * {"tool": "x", "params": {...}}          ← legacy
            * {"name": "x", "args": {...}}            ← function-call style

        Some also flatten tool_name to the top level of the envelope:

            * {"action": "tool_call", "tool_name": "x", "params": {...}}

        Returning a canonical dict means downstream consumers (PolicyEngine,
        ToolExecutor) only need to know one key. If we cannot figure out the
        tool name, we DEMOTE the action to "wait" rather than silently letting
        an empty target through the policy gate (which would then match an
        ALLOW-by-default rule).
        """
        params: Any = {}
        tool_name = ""

        if isinstance(content, dict):
            tool_name = str(
                content.get("tool_name")
                or content.get("tool")
                or content.get("name")
                or ""
            )
            params = (
                content.get("params")
                or content.get("args")
                or content.get("arguments")
                or {}
            )

        if not tool_name:
            tool_name = str(
                output.get("tool_name")
                or output.get("tool")
                or output.get("name")
                or ""
            )
            if not params:
                params = output.get("params") or output.get("args") or output.get("arguments") or {}

        if not tool_name:
            logger.warning("[Interpreter] tool_call without tool_name — demoting to 'wait'")
            return None, "wait"

        canonical = {"tool_name": tool_name, "params": params if isinstance(params, dict) else {}}
        return canonical, action

    def _check_needs_approval(self, action: str, _content: Any) -> bool:
        """
        Flag actions that must go through Human Approval gate.
        Brain decides — not LLM.
        """
        high_risk_actions = {"tool_call", "stop"}
        if action in high_risk_actions:
            return True
        return False
