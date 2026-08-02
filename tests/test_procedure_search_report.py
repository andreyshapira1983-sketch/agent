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
    ProcedureRecord,
    ProceduralMemoryStore,
    ProcedureSearchResult,
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


def test_a_candidate_only_store_reports_why_zero_surfaced(tmp_path: Path):
    """The measured black hole: every procedure is a candidate, so the maturity
    gate drops them all — and the report must SAY so, not just return zero."""
    store = _store(tmp_path, [
        _proc("read and enumerate", status="candidate", tags=("functions", "core")),
        _proc("another", status="candidate"),
    ])

    result = store.search_with_report("what functions does core/x define")

    assert isinstance(result, ProcedureSearchResult)
    assert result.procedures == []
    assert result.rejected_by.get("excluded_candidate") == 2
    # The distinction that was invisible before: this is NOT a no-overlap zero.
    assert "no_overlap" not in result.rejected_by


def test_no_overlap_is_distinct_from_the_maturity_gate(tmp_path: Path):
    store = _store(tmp_path, [
        _proc("totally unrelated", status="active", tags=("kubernetes",)),
    ])

    result = store.search_with_report("what functions does core/x define")

    assert result.procedures == []
    assert result.rejected_by.get("no_overlap") == 1
    assert "excluded_candidate" not in result.rejected_by


def test_an_active_matching_procedure_still_surfaces(tmp_path: Path):
    """Behaviour is unchanged for the case that already worked."""
    store = _store(tmp_path, [
        _proc("read and enumerate functions", status="active", tags=("functions",)),
        _proc("candidate noise", status="candidate", tags=("functions",)),
    ])

    result = store.search_with_report("list the functions")

    assert [p.name for p in result.procedures] == ["read and enumerate functions"]
    assert result.rejected_by.get("excluded_candidate") == 1


def test_search_still_returns_a_bare_list_for_existing_callers(tmp_path: Path):
    store = _store(tmp_path, [
        _proc("read and enumerate functions", status="active", tags=("functions",)),
    ])
    procs = store.search("list the functions")
    assert [p.name for p in procs] == ["read and enumerate functions"]


def test_empty_query_reports_no_query_tokens(tmp_path: Path):
    store = _store(tmp_path, [_proc("x", status="active")])
    result = store.search_with_report("")
    assert result.procedures == []
    assert result.rejected_by == {"no_query_tokens": 1}


def test_over_limit_counts_matches_beyond_the_cap(tmp_path: Path):
    """More matching procedures than the limit: the surplus is reported as
    `over_limit`, not silently dropped."""
    store = _store(tmp_path, [
        _proc(f"read and enumerate functions {i}", status="active", tags=("functions",))
        for i in range(5)
    ])

    result = store.search_with_report("list the functions", limit=2)

    assert len(result.procedures) == 2
    assert result.rejected_by.get("over_limit") == 3
    assert "no_overlap" not in result.rejected_by
