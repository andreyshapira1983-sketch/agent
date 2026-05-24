"""
tools/handler.py — Tool Handler (BrainLoop ↔ ToolExecutor bridge)

This is the glue between the Brain's `tool_call` action and actual tool execution.

When BrainLoop fires `on_tool_call`, the ToolHandler:
    1. Parses the tool name + params from ThinkResult.content
    2. Checks registry for approval requirements
    3. Runs the tool via ToolExecutor
    4. Submits ToolResult back into BrainLoop as a `tool_result` InputMessage
       → Brain sees it as new input and can reason about the result

Expected ThinkResult.content format for tool_call:
    {
        "tool_name": "calculator",
        "params": {"expression": "42 * 100"}
    }

Design:
    - Handler is stateless — one instance reused for all calls
    - All errors are captured and fed back to Brain as failed tool results
    - Approval-required tools pass through the BrainLoop approval gate
      (needs_human_approval=True is already set by Interpreter for tool_call)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .executor import ToolExecutor
from .registry import ToolNotFoundError, ToolRegistry

if TYPE_CHECKING:
    from brain.brain_loop import BrainLoop, InputMessage
    from brain.core import ThinkResult
    from brain.policy import PolicyEngine

logger = logging.getLogger(__name__)


class ToolHandler:
    """
    Async callback for BrainLoop.on_tool_call.

    Usage:
        handler = ToolHandler(registry, executor, loop)
        loop.on_tool_call = handler.handle
    """

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        loop: "BrainLoop",
        policy: "PolicyEngine | None" = None,
        autonomy_level: int = 2,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._loop = loop
        self._policy = policy
        self._autonomy_level = int(autonomy_level)

    async def handle(
        self,
        session_id: str,
        content: Any,
        result: "ThinkResult",
    ) -> None:
        """
        Called by BrainLoop when Brain issues a tool_call action.

        Args:
            session_id: Current session.
            content:    ThinkResult.content — should be dict with tool_name + params.
            result:     Full ThinkResult (for logging / context).
        """
        from brain.brain_loop import InputMessage  # avoid circular at module level

        # Step 1: Parse tool call spec
        tool_name, params, parse_error = _parse_content(content)
        if parse_error:
            logger.error("[ToolHandler] Parse error: %s | raw=%r", parse_error, content)
            await self._feed_back(session_id, tool_name or "unknown", False, None, parse_error)
            return

        # Step 2: Availability check
        if not self._registry.has(tool_name):
            err = f"Tool '{tool_name}' not found. Available: {self._registry.names()}"
            logger.warning("[ToolHandler] %s", err)
            await self._feed_back(session_id, tool_name, False, None, err)
            return

        # Step 3: Destructive check — only allow if human already approved
        # (BrainLoop's approval gate already ran — result.needs_human_approval was True)
        allow_destructive = self._registry.is_destructive(tool_name)

        # Step 3.5: PolicyEngine — second line of defence.
        #
        # The Brain already attached a `policy_verdict` upstream; this is the
        # physical block that runs even if the Brain layer is bypassed
        # (e.g. a future channel submits a tool_call directly into the loop).
        if self._policy is not None:
            from brain.policy import ActionRequest  # local import — soft dep

            request = ActionRequest(
                kind="tool_call",
                target=tool_name,
                is_destructive=allow_destructive,
                requires_approval_hint=self._registry.requires_approval(tool_name),
                autonomy_level=self._autonomy_level,
            )
            verdict = self._policy.evaluate(request)
            if verdict.denied:
                logger.warning(
                    "[ToolHandler] Policy DENIED '%s' (rule=%s reason=%s) | session=%s",
                    tool_name, verdict.rule_id, verdict.reason, session_id,
                )
                await self._feed_back(
                    session_id=session_id,
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=(
                        f"blocked by policy [{verdict.rule_id}]: {verdict.reason}"
                    ),
                )
                return
            if verdict.requires_approval and not result.needs_human_approval:
                # Approval flag was missed upstream — surface it now.
                logger.warning(
                    "[ToolHandler] Policy requires approval for '%s' but Brain didn't "
                    "mark needs_human_approval. Blocking until approved. session=%s",
                    tool_name, session_id,
                )
                await self._feed_back(
                    session_id=session_id,
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=(
                        f"approval required [{verdict.rule_id}]: {verdict.reason}"
                    ),
                )
                return

        # Step 4: Execute
        logger.info(
            "[ToolHandler] Executing '%s' | session=%s | params=%s",
            tool_name, session_id, list(params.keys()),
        )
        tool_result = self._executor.run(
            tool_name=tool_name,
            params=params,
            allow_destructive=allow_destructive,
        )

        # Step 5: Feed result back to Brain as new input
        await self._feed_back(
            session_id=session_id,
            tool_name=tool_name,
            success=tool_result.success,
            output=tool_result.output,
            error=tool_result.error,
        )

    # ------------------------------------------------------------------

    async def _feed_back(
        self,
        session_id: str,
        tool_name: str,
        success: bool,
        output: Any,
        error: str | None,
    ) -> None:
        """Submit tool result back into BrainLoop's input queue."""
        from brain.brain_loop import InputMessage

        if success:
            content = f"[tool_result] {tool_name} succeeded: {output}"
        else:
            content = f"[tool_result] {tool_name} failed: {error}"

        msg = InputMessage(
            content=content,
            session_id=session_id,
            source="tool_result",
            metadata={
                "tool_name": tool_name,
                "success": success,
                "output": output,
                "error": error,
            },
        )
        submitted = await self._loop.submit(msg)
        if not submitted:
            logger.warning(
                "[ToolHandler] Could not feed result back — loop queue full | tool=%s",
                tool_name,
            )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_content(content: Any) -> tuple[str | None, dict, str | None]:
    """
    Extract (tool_name, params, error) from ThinkResult.content.

    Returns:
        (tool_name, params, error_msg)
        error_msg is None on success.
    """
    if not isinstance(content, dict):
        return None, {}, f"tool_call content must be a dict, got {type(content).__name__}"

    tool_name = content.get("tool_name") or content.get("tool") or content.get("name")
    if not tool_name or not isinstance(tool_name, str):
        return None, {}, "tool_call content missing 'tool_name' key"

    params = content.get("params") or content.get("parameters") or content.get("args") or {}
    if not isinstance(params, dict):
        return tool_name, {}, f"'params' must be a dict, got {type(params).__name__}"

    return tool_name.strip(), params, None
