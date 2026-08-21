"""Run-scoped identity for one agent cycle.

- ``task_id`` names a **logical task**. It survives a retry: if a queued
task fails and is attempted again, both attempts carry the same task_id. -
``run_id`` names **one attempt**. It is minted per `AgentLoop.run` call and
is never reused.

Deliberately NOT here: `TraceLogger.trace_id`. That is created once per
agent in `build_agent`, so it identifies a *session*, not a run — every task
drained by one autonomous agent shares it.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RunContext:
    """Identity AND restrictions of the run currently executing here.

    The restrictions are the run's own and cannot widen what the host allows:
    every entry unions the blocked set and ORs the dry-run flag, so a nested or
    concurrent run may narrow further and never loosen. Nothing is written back
    onto the agent, which is what makes two overlapping runs independent — a
    ContextVar is per-execution-context, so one run's exit cannot restore over
    another run's restrictions.
    """

    run_id: str
    task_id: str | None = None
    #: Tools this run may not call, ON TOP OF whatever the host already blocks.
    blocked_tools: frozenset[str] = frozenset()
    #: True when THIS run must simulate effects. The host may also demand it.
    dry_run: bool = False


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


def identity_provenance(
    *,
    trace_id: str,
    run_id: str,
    task_id: str | None,
    session_id: str | None,
) -> dict[str, str | None]:
    """The provenance edge binding one run to its session, log and memory.

    Raises rather than emitting a half-empty edge: a record naming only one
    side reads as a connection while proving nothing, which is worse than an
    absent record because a consumer would trust it.
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

    Restrictions already in force are INHERITED: a run started inside a
    narrowed scope stays narrowed, so entering a fresh identity can never be a
    way to shed a limit.
    """
    outer = _RUN_CONTEXT.get()
    ctx = RunContext(
        run_id=run_id,
        task_id=task_id,
        blocked_tools=outer.blocked_tools if outer else frozenset(),
        dry_run=bool(outer.dry_run) if outer else False,
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

    `blocked_tools` is unioned and `dry_run` is ORed with whatever is already in
    force, so this cannot be used to lift a restriction — including the host's.
    """
    outer = _RUN_CONTEXT.get()
    ctx = RunContext(
        run_id=outer.run_id if outer else "unscoped",
        task_id=outer.task_id if outer else None,
        blocked_tools=(outer.blocked_tools if outer else frozenset())
        | frozenset(blocked_tools),
        dry_run=(bool(outer.dry_run) if outer else False) or bool(dry_run),
    )
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)
