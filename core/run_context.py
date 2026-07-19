"""Run-scoped identity for one agent cycle.

Two identifiers that are routinely confused, kept apart here:

- ``task_id`` names a **logical task**. It survives a retry: if a queued task
  fails and is attempted again, both attempts carry the same task_id.
- ``run_id`` names **one attempt**. It is minted per `AgentLoop.run` call and
  is never reused.

Neither may live in a mutable ``AgentLoop`` field. The agent is a long-lived
object — `api/server.py` shares one instance across requests — so a field would
be clobbered by overlapping runs and would leak into the next run if a cycle
raised. A ContextVar is scoped to the caller, restores on the error path, and
stays correct if the serialising lock in `api/server.py` is ever removed.

Deliberately NOT here: `TraceLogger.trace_id`. That is created once per agent
in `build_agent`, so it identifies a *session*, not a run — every task drained
by one autonomous agent shares it.
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


@contextmanager
def run_scope(run_id: str, task_id: str | None = None) -> Iterator[RunContext]:
    """Bind run identity for the duration of the block.

    Unwinds via ``reset(token)`` rather than re-setting to None, so a nested
    scope restores the *enclosing* run instead of erasing it. The reset happens
    in ``finally``, so an exception cannot leave stale identity behind.
    """
    ctx = RunContext(run_id=run_id, task_id=task_id)
    token = _RUN_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _RUN_CONTEXT.reset(token)
