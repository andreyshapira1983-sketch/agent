"""Procedure retrieval must explain a zero, like episodic retrieval does.

Phase 1 of the memory repair, step 0 — measure, don't change a decision yet.

The live 2026-08-02 learning probe logged `procedures_selected=0` with no
reason. Reproducing it on the store showed why the number was useless: a
relevant procedure WAS present but sat at `candidate`, and the maturity gate
excluded it — indistinguishable, in the journal, from a procedure that simply
did not match. `EpisodicMemoryStore.search_with_report` already reports its
drops; `ProceduralMemoryStore.search` returned a bare list. This closes that
asymmetry. It changes no selection decision — the same procedures surface —
only what the store is able to say about the rest.
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import (
    ProceduralMemoryStore,
    ProcedureRecord,
)


def _proc(name: str, *, status: str, tags: tuple[str, ...] = ()) -> ProcedureRecord:
    return ProcedureRecord(
        name=name,
        workflow_key="tools:file_read",
        trigger_tags=tags,
        steps=("Situation: x", "Run tool: file_read"),
        lessons=(),
        source_episode_ids=(),
        success_count=1,
        failure_count=0,
        confidence=0.667,
        status=status,
    )


def _store(tmp_path: Path, procs: list[ProcedureRecord]) -> ProceduralMemoryStore:
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")
    store.rewrite(procs)
    return store


def test_empty_query_reports_no_query_tokens(tmp_path: Path):
    store = _store(tmp_path, [_proc("x", status="active")])
    result = store.search_with_report("")
    assert result.procedures == []
    assert result.rejected_by == {"no_query_tokens": 1}
