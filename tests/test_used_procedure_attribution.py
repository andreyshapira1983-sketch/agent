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

from pathlib import Path

import pytest

from core.smart_memory import (
    EpisodeRecord,
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
# THE central invariant: identical workflow_key, only one actually used.
# ==========================================================================
def test_same_workflow_key_only_the_used_procedure_is_attributed() -> None:
    """Executable proof that MIR-049 removes the risk MIR-050 identified.

    Two procedures share `tools:file_read`. Only one was selected into this
    run. Attribution must follow the selection, never the shared key — that
    key is exactly what MIR-050 measured to pool unrelated goals.
    """
    used_one = _proc("proc-selected", tools=("file_read",), name="Read the changelog")
    never_ran = _proc("proc-other", tools=("file_read",), name="Read the licence file")
    assert used_one.workflow_key == never_ran.workflow_key, "precondition: same key"

    attributed = resolve_used_procedures(
        selected=[used_one],                 # only this one reached planning
        executed_tools=["file_read"],
    )

    assert attributed == ("proc-selected",)
    assert "proc-other" not in attributed, (
        "a procedure that was never selected must not be debited merely for "
        "sharing a workflow_key with one that was"
    )


# ==========================================================================
# Selection and execution are separate gates.
# ==========================================================================
def test_retrieved_but_not_selected_is_not_attributed() -> None:
    assert resolve_used_procedures(selected=[], executed_tools=["file_read"]) == ()


def test_selected_but_execution_never_reached_it() -> None:
    """The plan is not evidence: a cancelled run must not debit a procedure."""
    proc = _proc("p", tools=("file_read", "list_dir"), name="Read then list")

    assert resolve_used_procedures(selected=[proc], executed_tools=[]) == ()
    # partial execution is still not application
    assert resolve_used_procedures(selected=[proc], executed_tools=["file_read"]) == ()


def test_fully_executed_workflow_is_attributed() -> None:
    proc = _proc("p", tools=("file_read", "list_dir"), name="Read then list")

    assert resolve_used_procedures(
        selected=[proc], executed_tools=["file_read", "list_dir"]
    ) == ("p",)


def test_repeated_use_is_recorded_once() -> None:
    proc = _proc("p", tools=("file_read",), name="Read")

    assert resolve_used_procedures(
        selected=[proc], executed_tools=["file_read", "file_read", "file_read"]
    ) == ("p",)


def test_two_procedures_both_attributed_in_deterministic_order() -> None:
    first = _proc("p-first", tools=("file_read",), name="Read")
    second = _proc("p-second", tools=("shell_exec",), name="Run")

    got = resolve_used_procedures(
        selected=[second, first], executed_tools=["file_read", "shell_exec"]
    )

    assert set(got) == {"p-first", "p-second"}
    assert got == ("p-first", "p-second"), (
        "order must be first actual use, and stable — a serialised set would "
        "make the record non-reproducible"
    )
    assert isinstance(got, tuple), "must not serialise as a set"


# ==========================================================================
# Attribution survives bad endings.
# ==========================================================================
@pytest.mark.parametrize("outcome", ["partial", "failed"])
def test_attribution_survives_a_bad_outcome(outcome: str) -> None:
    """A procedure that really ran stays attributed however the run ended.

    How partial/failed affect the counters is MIR-048's decision; losing the
    link would remove the evidence that decision needs.
    """
    proc = _proc("p", tools=("shell_exec",), name="Run")
    episode = EpisodeRecord(
        goal="g", question="q", outcome=outcome,  # type: ignore[arg-type]
        summary="s", tools_used=("shell_exec",),
        used_procedure_ids=resolve_used_procedures(
            selected=[proc], executed_tools=["shell_exec"]
        ),
    )

    assert episode.used_procedure_ids == ("p",)
    assert EpisodeRecord.from_dict(episode.to_dict()).used_procedure_ids == ("p",)


def test_cancellation_after_application_keeps_the_link() -> None:
    proc = _proc("p", tools=("file_read",), name="Read")
    # the run died after this tool completed
    assert resolve_used_procedures(selected=[proc], executed_tools=["file_read"]) == ("p",)


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


def test_legacy_episode_reads_as_unknown_not_empty() -> None:
    """None and () must stay distinguishable — they license different actions."""
    legacy = EpisodeRecord.from_dict(
        {"id": "old", "goal": "g", "question": "q", "outcome": "success", "summary": "s"}
    )
    assert legacy.used_procedure_ids is None, (
        "an absent field means attribution is UNKNOWN; recording it as () "
        "would assert that no procedure ran, which was never established"
    )

    known_none = EpisodeRecord.from_dict(
        {"id": "new", "goal": "g", "question": "q", "outcome": "success",
         "summary": "s", "used_procedure_ids": []}
    )
    assert known_none.used_procedure_ids == ()


def test_unknown_procedure_id_is_not_substituted_via_workflow_key() -> None:
    """Attribution names procedures, not shapes.

    If the referenced procedure is later deleted, the id stays dangling — it is
    never quietly re-pointed at whatever else shares its workflow.
    """
    episode = EpisodeRecord(
        goal="g", question="q", outcome="success",  # type: ignore[arg-type]
        summary="s", used_procedure_ids=("proc-deleted",),
    )
    restored = EpisodeRecord.from_dict(episode.to_dict())

    assert restored.used_procedure_ids == ("proc-deleted",)
