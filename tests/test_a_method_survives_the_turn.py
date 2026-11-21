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
