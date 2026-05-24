"""
brain/brain_loop.py — The Brain's Continuous Execution Loop

This is the engine that drives the Brain.
Everything built so far (Core, Memory, Planner, Interpreter) runs from here.

Architecture:
    - Async (asyncio) — ready for Telegram, API, or any async interface
    - Input Queue — messages pushed in from outside, Brain processes them in order
    - Action Dispatcher — routes ThinkResult.action to the right handler
    - Planner Driver — when Brain creates a plan, loop drives step execution
    - Graceful shutdown — finishes current cycle before stopping
    - Error isolation — one bad input never kills the loop

Flow:
    External world pushes message → InputQueue
    BrainLoop pulls from queue → Brain.think() → ThinkResult
    ActionDispatcher routes result:
        "respond"   → on_response callback
        "tool_call" → on_tool_call callback (requires Human Approval if flagged)
        "wait"      → skip, log
        "stop"      → shutdown
        "clarify"   → on_response with clarification request

    If Brain produced a Plan → PlannerDriver drives step execution
    After each step → Brain.think() re-evaluates
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable

from .core import Brain, ThinkResult
from .planner import Planner

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Loop state
# ------------------------------------------------------------------

class LoopState(str, Enum):
    IDLE     = "idle"
    RUNNING  = "running"
    STOPPING = "stopping"
    STOPPED  = "stopped"


@dataclass
class LoopStats:
    started_at: datetime | None = None
    cycles: int = 0
    errors: int = 0
    last_input: str | None = None
    last_action: str | None = None


# ------------------------------------------------------------------
# Input message
# ------------------------------------------------------------------

@dataclass
class InputMessage:
    content: str
    session_id: str
    source: str = "user"           # "user" | "scheduler" | "tool_result"
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


# ------------------------------------------------------------------
# Callbacks type aliases
# ------------------------------------------------------------------

ResponseCallback  = Callable[[str, str, ThinkResult], Awaitable[None]]
ToolCallCallback  = Callable[[str, Any, ThinkResult], Awaitable[None]]
ApprovalCallback  = Callable[[ThinkResult], Awaitable[bool]]


# ------------------------------------------------------------------
# BrainLoop
# ------------------------------------------------------------------

class BrainLoop:
    """
    Continuous async execution loop for the Brain.

    Usage:
        loop = BrainLoop(brain=brain, planner=planner)
        loop.on_response = my_send_message_function
        await loop.start()
        await loop.submit(InputMessage(...))
        ...
        await loop.stop()
    """

    MAX_QUEUE_SIZE   = 100   # Default — override via __init__ parameter
    CYCLE_TIMEOUT    = 30.0  # Max seconds Brain gets per cycle
    PLANNER_TIMEOUT  = 5.0   # Max seconds per planner step

    def __init__(
        self,
        brain: Brain,
        planner: Planner,
        max_queue_size: int | None = None,
    ) -> None:
        self._brain    = brain
        self._planner  = planner
        queue_size = max_queue_size if max_queue_size is not None else self.MAX_QUEUE_SIZE
        self._queue: asyncio.Queue[InputMessage | _Sentinel] = asyncio.Queue(maxsize=queue_size)
        self._state    = LoopState.IDLE
        self._stats    = LoopStats()
        self._task: asyncio.Task | None = None

        # Callbacks — set by the external interface (Telegram bot, API, etc.)
        self.on_response:  ResponseCallback | None = None
        self.on_tool_call: ToolCallCallback | None = None
        self.on_approval:  ApprovalCallback | None = None   # called when needs_human_approval

        logger.info("[BrainLoop] Initialized | queue_size=%d", queue_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the loop. Non-blocking — runs as background asyncio task."""
        if self._state != LoopState.IDLE:
            logger.warning("[BrainLoop] Already running or stopped")
            return

        self._state = LoopState.RUNNING
        self._stats.started_at = datetime.utcnow()
        self._task = asyncio.create_task(self._run(), name="brain-loop")
        logger.info("[BrainLoop] Started")

    async def stop(self, timeout: float = 5.0) -> None:
        """
        Graceful shutdown.
        Waits for current cycle to finish, then stops.
        """
        if self._state != LoopState.RUNNING:
            return

        logger.info("[BrainLoop] Stopping...")
        self._state = LoopState.STOPPING

        # Send sentinel to unblock queue.get()
        await self._queue.put(_STOP_SENTINEL)

        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("[BrainLoop] Force-cancelling after timeout")
                self._task.cancel()

        self._state = LoopState.STOPPED
        logger.info("[BrainLoop] Stopped | cycles=%d errors=%d", self._stats.cycles, self._stats.errors)

    async def submit(self, message: InputMessage) -> bool:
        """
        Push a message into the input queue.
        Returns False if queue is full (message dropped).
        """
        if self._state != LoopState.RUNNING:
            logger.warning("[BrainLoop] Cannot submit — loop not running (state=%s)", self._state)
            return False

        try:
            self._queue.put_nowait(message)  # type: ignore[arg-type]
            logger.debug("[BrainLoop] Queued | session=%s source=%s", message.session_id, message.source)
            return True
        except asyncio.QueueFull:
            logger.error("[BrainLoop] Queue full — message dropped | session=%s", message.session_id)
            return False

    def state(self) -> LoopState:
        return self._state

    def stats(self) -> dict:
        return {
            "state": self._state.value,
            "cycles": self._stats.cycles,
            "errors": self._stats.errors,
            "started_at": self._stats.started_at.isoformat() if self._stats.started_at else None,
            "last_action": self._stats.last_action,
            "queue_size": self._queue.qsize(),
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._state == LoopState.RUNNING:
            try:
                message = await self._queue.get()

                # Sentinel = stop signal
                if isinstance(message, _Sentinel):
                    break

                await self._process(message)
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught,broad-except
                self._stats.errors += 1
                logger.error("[BrainLoop] Unhandled error in loop: %s", exc, exc_info=True)

        self._state = LoopState.STOPPED

    async def _process(self, message: InputMessage) -> None:
        """One full cycle: input → Brain.think() → dispatch action."""
        self._stats.cycles += 1
        self._stats.last_input = message.content
        logger.info(
            "[BrainLoop] Cycle #%d | session=%s source=%s",
            self._stats.cycles, message.session_id, message.source,
        )

        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._brain.think,
                    message.content,
                    message.session_id,
                ),
                timeout=self.CYCLE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("[BrainLoop] Brain.think() timed out after %.1fs", self.CYCLE_TIMEOUT)
            self._stats.errors += 1
            return

        self._stats.last_action = result.action
        await self._dispatch(result, message)

        # If Brain created a plan — drive it
        if self._planner.has_active_plan():
            await self._drive_planner(session_id=message.session_id)

    # ------------------------------------------------------------------
    # Action dispatcher
    # ------------------------------------------------------------------

    async def _dispatch(self, result: ThinkResult, message: InputMessage) -> None:
        """Route ThinkResult.action to the right handler."""

        # Human approval gate — checked first
        approval_cb = self.on_approval
        if result.needs_human_approval and callable(approval_cb):
            # Surface the explanation (if Brain produced one) so the callback
            # can show the full justification to the human before asking.
            if result.explanation:
                logger.info(
                    "[BrainLoop] Approval required | risk=%s\n%s",
                    result.explanation.risk_level.value,
                    result.explanation.for_human_approval(),
                )
            approved = await approval_cb(result)  # pylint: disable=not-callable
            if not approved:
                logger.info("[BrainLoop] Action blocked by Human Approval | action=%s", result.action)
                response_cb = self.on_response
                if callable(response_cb):
                    await response_cb(  # pylint: disable=not-callable
                        message.session_id,
                        "Action requires your approval. Cancelled.",
                        result,
                    )
                return

        action = result.action

        if action == "respond" or action == "clarify":
            response_cb = self.on_response
            if callable(response_cb) and result.content:
                await response_cb(message.session_id, str(result.content), result)  # pylint: disable=not-callable

        elif action == "tool_call":
            tool_cb = self.on_tool_call
            if callable(tool_cb) and result.content:
                await tool_cb(message.session_id, result.content, result)  # pylint: disable=not-callable

        elif action == "wait":
            logger.info("[BrainLoop] Action=wait | reasoning=%s", result.reasoning)

        elif action == "stop":
            logger.info("[BrainLoop] Action=stop — initiating shutdown")
            await self.stop()

        else:
            logger.warning("[BrainLoop] Unknown action '%s' — ignoring", action)

    # ------------------------------------------------------------------
    # Planner driver
    # ------------------------------------------------------------------

    async def _drive_planner(self, session_id: str) -> None:
        """
        Execute the active Plan step by step.
        Each step is processed, result fed back to Brain.
        Loop continues until plan is complete or fails.
        """
        logger.info("[BrainLoop] Driving planner | session=%s", session_id)

        while self._planner.has_active_plan():
            step = self._planner.next_step()
            if step is None:
                break

            logger.info("[BrainLoop] Executing step id=%d action=%s", step.id, step.action)

            # Dispatch step as tool call — tool layer handles actual execution
            if self.on_tool_call:
                try:
                    await asyncio.wait_for(
                        self.on_tool_call(
                            session_id,
                            {"step_id": step.id, "action": step.action, "params": step.params},
                            ThinkResult(
                                action="tool_call",
                                content=step.to_dict(),
                                confidence=1.0,
                                reasoning=f"Planner step: {step.description}",
                            ),
                        ),
                        timeout=self.PLANNER_TIMEOUT,
                    )
                    self._planner.mark_done(step.id, result="dispatched")
                except asyncio.TimeoutError:
                    self._planner.mark_failed(step.id, error="Timeout", skip_dependents=True)
                    logger.error("[BrainLoop] Planner step timed out | id=%d", step.id)
                except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught,broad-except
                    self._planner.mark_failed(step.id, error=str(exc), skip_dependents=True)
                    logger.error("[BrainLoop] Planner step error | id=%d error=%s", step.id, exc)
            else:
                # No tool handler registered — skip step
                self._planner.mark_skipped(step.id)
                logger.warning("[BrainLoop] No on_tool_call registered — step skipped id=%d", step.id)

            progress = self._planner.progress()
            if progress:
                logger.info("[BrainLoop] Plan progress: %d%%", progress["percent"])


# ------------------------------------------------------------------
# Sentinel object for queue shutdown
# ------------------------------------------------------------------

class _Sentinel:
    pass

_STOP_SENTINEL = _Sentinel()
