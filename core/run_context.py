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
    """Identity of the run currently executing on this context."""

    run_id: str
    task_id: str | None = None


_RUN_CONTEXT: ContextVar[RunContext | None] = ContextVar(
    "agent_run_context", default=None
)


def current_run() -> RunContext | None:
    """The active run's identity, or None outside any run."""
    return _RUN_CONTEXT.get()


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
    """Bind run identity for the duration of the block."""
    ctx = RunContext(run_id=run_id, task_id=task_id)
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)
