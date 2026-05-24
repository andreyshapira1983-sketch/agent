"""
tools/executor.py — Tool Executor

The Executor is the only component that actually runs tools.
Brain says "call tool X with params Y" → Executor handles:
    - Lookup in registry
    - Param validation
    - Execution with timeout
    - Error isolation (tool crashes don't crash the agent)
    - Result logging

Security:
    - No tool runs without registry membership
    - Destructive tools require explicit allow_destructive flag
    - Execution timeout prevents runaway tools
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .base import ToolResult
from .registry import ToolNotFoundError, ToolRegistry

try:
    from brain.state.idempotency import IdempotencyStore
except ImportError:
    IdempotencyStore = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 30.0  # Hard cap per tool call


class ToolExecutor:
    """
    Executes tools on behalf of the Brain.

    Usage:
        executor = ToolExecutor(registry)
        result = executor.run("web_search", query="Python asyncio", timeout=10)
    """

    def __init__(
        self,
        registry: ToolRegistry,
        default_timeout: float = DEFAULT_TIMEOUT_SEC,
        idempotency_store: "IdempotencyStore | None" = None,
    ) -> None:
        self._registry = registry
        self._default_timeout = default_timeout
        self._idempotency = idempotency_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        allow_destructive: bool = False,
        *,
        task_id: str | None = None,
        step_id: str | int | None = None,
    ) -> ToolResult:
        """
        Execute a tool by name with given params.

        Args:
            tool_name:        Name as registered in ToolRegistry.
            params:           Keyword arguments forwarded to tool.execute().
            timeout:          Per-call timeout in seconds (overrides default).
            allow_destructive: Must be True to run is_destructive tools.
            task_id:          Optional task context for idempotency tracking.
            step_id:          Optional step ID within the task plan.

        Returns:
            ToolResult — always. Never raises.
        """
        params = params or {}
        deadline = timeout if timeout is not None else self._default_timeout

        # Step 1: Lookup
        try:
            tool = self._registry.get(tool_name)
        except ToolNotFoundError as exc:
            logger.error("[Executor] %s", exc)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=str(exc),
            )

        # Step 2: Destructive guard
        if tool.spec.is_destructive and not allow_destructive:
            msg = (
                f"Tool '{tool_name}' is destructive. "
                "Set allow_destructive=True after human approval."
            )
            logger.warning("[Executor] %s", msg)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=msg,
            )

        # Step 2.5: Idempotency check — skip if already ran successfully
        idem_key: str | None = None
        if self._idempotency is not None and task_id and step_id is not None:
            idem_key = self._idempotency.make_key(task_id, step_id, tool_name, params)
            cached = self._idempotency.check(idem_key)
            if cached is not None:
                logger.info(
                    "[Executor] '%s' served from idempotency cache | task=%s step=%s",
                    tool_name, task_id[:8] if task_id else "", step_id,
                )
                return cached.to_tool_result()

        # Step 3: Execute with timeout
        logger.info(
            "[Executor] Running '%s' | params=%s | timeout=%.1fs",
            tool_name, list(params.keys()), deadline,
        )
        result = self._run_with_timeout(tool, params, deadline)

        if result.success:
            logger.info("[Executor] '%s' OK | duration=%.0fms", tool_name,
                        result.metadata.get("duration_ms", 0))
            # Step 3.5: Cache successful result for idempotency
            if self._idempotency is not None and idem_key and task_id and step_id is not None:
                self._idempotency.save(idem_key, task_id, step_id, tool_name, result)
        else:
            logger.warning("[Executor] '%s' FAILED | error=%s", tool_name, result.error)

        return result

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _run_with_timeout(
        self,
        tool,
        params: dict[str, Any],
        timeout: float,
    ) -> ToolResult:
        """Run tool.execute(**params) in a thread with a hard timeout."""
        container: list[ToolResult] = []
        exc_container: list[BaseException] = []

        def _worker():
            try:
                r = tool.execute(**params)
                container.append(r)
            except Exception as exc:  # noqa: BLE001
                exc_container.append(exc)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            # Thread is still running — timeout hit
            return ToolResult(
                tool_name=tool.spec.name,
                success=False,
                output=None,
                error=f"Tool '{tool.spec.name}' timed out after {timeout:.1f}s",
                metadata={"timed_out": True},
            )

        if exc_container:
            # Unexpected exception inside tool — isolate it
            err = repr(exc_container[0])
            logger.exception("[Executor] Unexpected error in '%s'", tool.spec.name)
            return ToolResult(
                tool_name=tool.spec.name,
                success=False,
                output=None,
                error=f"Unexpected error: {err}",
                metadata={"exception": err},
            )

        return container[0]
