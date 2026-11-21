"""MIR-049 — record which procedures a run actually used, and only those.

`used_procedure_ids` means: procedures that really influenced THIS run. It is
deliberately not "everything retrieval returned", "everything shown to the
planner", or "everything that looked applicable". MIR-048 will debit these
identifiers, so anything looser turns feedback into misattributed punishment —
counting a failure against a procedure that had nothing to do with it.

Usage is judged from EXECUTION, not from the plan: a run cancelled before it
reached a procedure's steps must not debit that procedure. Concretely, a
procedure counts as used once every tool in its workflow has actually run.

Three states, kept distinct because they answer different questions:

    None    legacy row — attribution unknown, nothing may be inferred
    ()      this version ran and is certain no procedure was applied
    (ids…)  application observed

MIR-048 must never re-derive attribution for a `None` row via workflow_key —
that would route around the entire point of this change.

This change only OBSERVES. Counters stay untouched, so "is the attribution
correct" can be proven separately from "is the feedback policy correct".

Status when written: all fail — `EpisodeRecord` has no `used_procedure_ids`.
"""
from __future__ import annotations

from core.smart_memory import (
    ProcedureRecord,
    resolve_used_procedures,
)


def _proc(pid: str, *, tools: tuple[str, ...], name: str, steps: tuple[str, ...] = ()) -> ProcedureRecord:
    return ProcedureRecord(
        name=name,
        workflow_key="tools:" + "->".join(tools),
        trigger_tags=(), steps=steps or (f"Run tool: {t}" for t in tools),  # type: ignore[arg-type]
        id=pid, success_count=1, failure_count=0, confidence=0.667,
    )


# ==========================================================================
# Replay and legacy.
# ==========================================================================
def test_replay_attributes_nothing() -> None:
    """A replay re-serves an old answer; it applies no procedure now.

    Otherwise re-showing one cached answer would inflate a procedure's record
    every time it was shown.
    """
    proc = _proc("p", tools=("file_read",), name="Read")

    assert resolve_used_procedures(selected=[proc], executed_tools=[]) == ()
