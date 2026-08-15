"""Being offered is not credit — and without it nothing is ever earned.

Background: docs/CODE_NOTES.md, "The method that could not survive the turn".
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import ProceduralMemoryStore, ProcedureRecord


def _proc(key: str, status: str, confidence: float = 0.667) -> ProcedureRecord:
    return ProcedureRecord(
        name=f"поиск через {key}",
        workflow_key=f"tools:{key}",
        trigger_tags=("grep", "поиск", "сигнал"),
        steps=("Situation: найти, где рождается сигнал", f"Run tool: {key}"),
        status=status,
        confidence=confidence,
    )


def test_a_candidate_is_offered_so_it_can_ever_be_used(tmp_path: Path):
    """Measured 2026-08-15: 30 of 31 procedures were `candidate`, one active.

    The deadlock, exactly: a new procedure is born a candidate; candidates were
    withheld from the planner; never offered means never applied; never applied
    means no causal credit; no credit means it stays a candidate. Thirty were
    stuck in that circle.

    Live consequence: the agent found the right method (`grep`) one turn, and
    the next turn planned four blind `file_read`s instead. The method could not
    survive the turn because the mechanism for surviving it was sealed shut.
    """
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([_proc("shell_exec", "candidate")])

    result = store.search_with_report("найти сигнал поиском grep")

    assert result.procedures, (
        "a candidate was withheld from the planner, so it can never be applied "
        "and can never stop being a candidate"
    )


def test_an_offered_candidate_is_marked_unproven(tmp_path: Path):
    """Offering is not endorsing. The planner must see which is which, or the
    gate would be traded for a lie.
    """
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([_proc("shell_exec", "candidate")])

    offered = store.search_with_report("найти сигнал поиском grep").procedures

    assert offered[0].status == "candidate"


def test_proven_procedures_outrank_candidates(tmp_path: Path):
    """The maturity gate's intent survives: unproven never displaces proven."""
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([
        _proc("shell_exec", "candidate", confidence=0.9),
        _proc("grep_then_read", "active", confidence=0.7),
    ])

    offered = store.search_with_report("найти сигнал поиском grep").procedures

    assert offered[0].status == "active", (
        "a candidate outranked a proven procedure — the gate protected against "
        "exactly this, and only its visibility half was meant to change"
    )


def test_a_retired_procedure_stays_out(tmp_path: Path):
    """`obsolete` is a decision already taken; visibility does not reopen it."""
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([_proc("shell_exec", "obsolete")])

    assert store.search_with_report("найти сигнал поиском grep").procedures == []


def test_the_report_still_says_why_the_rest_were_dropped(tmp_path: Path):
    """The counter that made this measurable must keep working."""
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([_proc("shell_exec", "obsolete"), _proc("other", "candidate")])

    report = store.search_with_report("найти сигнал поиском grep")

    assert "excluded_candidate" not in report.rejected_by
    assert report.rejected_by.get("excluded_retired") == 1
