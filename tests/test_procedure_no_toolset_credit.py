"""A tool-set match is not usefulness (operator ruling 2026-08-02).

Measured on the live store: a candidate `read-and-enumerate` procedure
(workflow_key `tools:file_read`) was promoted to `active` by a completely
unrelated `fix the parser bug` run that merely used `file_read` and never
applied the procedure. That is coincidental tool-pattern reinforcement, not
learning. The ruling: credit flows only through the causal `used_procedure_ids`
path; `upsert_from_episode` merges provenance but grants no credit.
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import (
    EpisodeRecord,
    ProceduralMemoryStore,
    ProcedureRecord,
)


def _candidate(**over) -> ProcedureRecord:
    base = {
        "name": "read and enumerate",
        "workflow_key": "tools:file_read",
        "trigger_tags": ("functions",),
        "steps": ("Situation: x", "Run tool: file_read"),
        "lessons": (),
        "source_episode_ids": (),
        "success_count": 1,
        "failure_count": 0,
        "confidence": 0.667,
        "status": "candidate",
    }
    base.update(over)
    return ProcedureRecord(**base)


def _episode(**over) -> EpisodeRecord:
    base = {
        "goal": "fix the parser bug",
        "question": "fix the parser bug",
        "summary": "read and fixed",
        "outcome": "success",
        "tools_used": ("file_read",),
        "completion_state": "achieved",
        "declared_completion": "achieved",
        "used_procedure_ids": (),  # the procedure was NOT applied
    }
    base.update(over)
    return EpisodeRecord(**base)


def _store(tmp_path: Path, procs: list[ProcedureRecord]) -> ProceduralMemoryStore:
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")
    store.rewrite(procs)
    return store


def test_a_toolset_match_does_not_promote_a_candidate(tmp_path: Path):
    """The reproduction, locked. An unrelated success that shares the tool set
    must not move the candidate's counters or status."""
    store = _store(tmp_path, [_candidate(success_count=1, status="candidate")])

    updated, created = store.upsert_from_episode(_episode(id="ep-unrelated"))

    assert created is False
    assert updated.status == "candidate", "a tool-set match promoted the candidate"
    assert updated.success_count == 1, "a tool-set match credited the candidate"
    assert updated.confidence == _candidate().confidence


def test_the_match_still_merges_provenance(tmp_path: Path):
    """Consolidation is kept: the merged episode's id is recorded, so the store
    still holds one procedure per workflow_key — it just isn't credited."""
    store = _store(tmp_path, [
        _candidate(status="active", success_count=2, source_episode_ids=("ep-old",))
    ])

    updated, _ = store.upsert_from_episode(_episode(id="ep-new"))

    assert "ep-new" in updated.source_episode_ids
    assert "ep-old" in updated.source_episode_ids
    assert updated.success_count == 2  # unchanged — provenance merged, not credited


def test_a_newborn_candidate_starts_unproven(tmp_path: Path):
    """A procedure minted from a run is born unproven: the creating run did not
    USE it (it did not exist yet), so it earns no birth credit."""
    store = _store(tmp_path, [])

    updated, created = store.upsert_from_episode(
        _episode(id="ep-birth", tools_used=("file_read",))
    )

    assert created is True
    assert updated.status == "candidate"
    assert updated.success_count == 0, "a newborn candidate was credited for its own creation"


def test_causal_credit_still_promotes(tmp_path: Path):
    """The legitimate path is untouched: a run that actually USED the procedure
    and succeeded credits it through apply_episode_feedback."""
    proc = _candidate(status="candidate", success_count=1)
    store = _store(tmp_path, [proc])

    used = _episode(
        id="ep-used",
        tools_used=("file_read",),
        used_procedure_ids=(proc.id,),
    )
    report = store.apply_episode_feedback(used)

    after = {p.id: p for p in store.load()}[proc.id]
    assert after.success_count == 2, f"causal credit did not apply: {report}"
    assert after.status == "active"
