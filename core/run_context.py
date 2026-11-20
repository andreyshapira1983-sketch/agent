"""Run-scoped identity and restrictions for one agent cycle (task_id spans retries, run_id is one attempt)."""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RunContext:
    """Identity AND restrictions of the run currently executing here.

    Restrictions only narrow what the host allows: entries union the blocked set and OR the dry-run flag. Nothing is written onto the agent, and a ContextVar is per execution context, so overlapping runs stay independent.
    """

    run_id: str
    task_id: str | None = None
    # Tools this run may not call, on top of the host's blocks.
    blocked_tools: frozenset[str] = frozenset()
    # True when THIS run must simulate effects; the host may also demand it.
    dry_run: bool = False
    # Absolute value the session cost counter may reach during this run; None = no bound.
    # Consulted by the model-call pre-flight gate, so the bound acts before the next spend.
    cost_ceiling_units: int | None = None
    # Run start time (time.time()), used to tell what a turn generated from what it used; 0 =
    # outside a run.
    # Not part of equality: time is not run identity.
    started_at: float = field(default=0.0, compare=False)


_RUN_CONTEXT: ContextVar[RunContext | None] = ContextVar(
    "agent_run_context", default=None
)


def current_run() -> RunContext | None:
    """The active run's identity, or None outside any run."""
    return _RUN_CONTEXT.get()


def run_blocked_tools() -> frozenset[str]:
    """What the current run forbids beyond the host. Empty outside a run."""
    ctx = _RUN_CONTEXT.get()
    return ctx.blocked_tools if ctx is not None else frozenset()


def run_demands_dry_run() -> bool:
    """True when the current run requires effects to be simulated."""
    ctx = _RUN_CONTEXT.get()
    return bool(ctx.dry_run) if ctx is not None else False


def run_cost_ceiling() -> int | None:
    """The session-cost total this run may not pass, or None outside one."""
    ctx = _RUN_CONTEXT.get()
    return ctx.cost_ceiling_units if ctx is not None else None


def identity_provenance(
    *,
    trace_id: str,
    run_id: str,
    task_id: str | None,
    session_id: str | None,
) -> dict[str, str | None]:
    """The provenance edge binding one run to its session, log and memory.

    Raises rather than emitting a half-empty edge, which a consumer would trust while it proves nothing.
    """
    if not trace_id or not run_id:
        raise ValueError(
            "an identity edge needs both sides: "
            f"trace_id={trace_id!r} run_id={run_id!r}"
        )
    return {
        "trace_id": trace_id,
        "run_id": run_id,
        "task_id": task_id,
        "session_id": session_id,
    }


@contextmanager
def run_scope(run_id: str, task_id: str | None = None) -> Iterator[RunContext]:
    """Bind run identity for the duration of the block.

    Restrictions already in force are inherited, so a fresh identity cannot shed a limit.
    """
    outer = _RUN_CONTEXT.get()
    ctx = RunContext(
        run_id=run_id,
        task_id=task_id,
        blocked_tools=outer.blocked_tools if outer else frozenset(),
        dry_run=bool(outer.dry_run) if outer else False,
        cost_ceiling_units=outer.cost_ceiling_units if outer else None,
        started_at=time.time(),
    )
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)


@contextmanager
def run_restrictions(
    *,
    blocked_tools: frozenset[str] | set[str] = frozenset(),
    dry_run: bool = False,
) -> Iterator[RunContext]:
    """Narrow the current run for the duration of the block, never widen it.

    `blocked_tools` is unioned and `dry_run` ORed with what is already in force, including the host's.
    """
    outer = _RUN_CONTEXT.get()
    ctx = RunContext(
        run_id=outer.run_id if outer else "unscoped",
        task_id=outer.task_id if outer else None,
        blocked_tools=(outer.blocked_tools if outer else frozenset())
        | frozenset(blocked_tools),
        dry_run=(bool(outer.dry_run) if outer else False) or bool(dry_run),
        cost_ceiling_units=outer.cost_ceiling_units if outer else None,
        started_at=outer.started_at if outer else 0.0,
    )
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)


@contextmanager
def run_cost_envelope(*, allowed_total_units: int) -> Iterator[RunContext]:
    """Bound the session cost counter for the duration of the block.

    `allowed_total_units` is the ABSOLUTE value the counter may reach (current count plus what this run may still spend); a nested envelope takes the minimum with the ceiling in force.
    """
    outer = _RUN_CONTEXT.get()
    requested = int(allowed_total_units)
    if outer is not None and outer.cost_ceiling_units is not None:
        requested = min(requested, outer.cost_ceiling_units)
    ctx = RunContext(
        run_id=outer.run_id if outer else "unscoped",
        task_id=outer.task_id if outer else None,
        blocked_tools=outer.blocked_tools if outer else frozenset(),
        dry_run=bool(outer.dry_run) if outer else False,
        cost_ceiling_units=requested,
        started_at=outer.started_at if outer else 0.0,
    )
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)
