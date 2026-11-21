"""Средние дефекты памяти (проверка 17.11, средняя): rewrite-гонка, починка статуса, id в подсказке."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    ProcedureRecord,
    _smoothed_confidence,
)
from scripts.demote_unverifiable_procedure_standing import select_and_demote


def _proc(pid: str, key: str, *, s: int = 0, f: int = 0, status: str = "candidate", **kw) -> ProcedureRecord:
    return ProcedureRecord(
        id=pid, name=pid, workflow_key=key, trigger_tags=(), steps=(),
        success_count=s, failure_count=f, confidence=_smoothed_confidence(s, f),
        status=status, **kw,
    )


def _episode(eid: str, **kw) -> EpisodeRecord:
    base: dict = {
        "id": eid, "goal": "g", "question": "read the config file please", "outcome": "success",
        "summary": "s", "tools_used": ("file_read",), "source_labels": ("file:a",),
        "verified_chunks": 2, "completion_state": "achieved",
    }
    base.update(kw)
    return EpisodeRecord(**base)


# --- 1. read-modify-write under one lock ---------------------------------------------

def test_update_procedures_keeps_credit_and_new_rows_written_in_between(tmp_path: Path) -> None:
    s = ProceduralMemoryStore(tmp_path / "p.jsonl")
    s.rewrite([_proc("p1", "tools:file_read", s=1)])
    s.apply_episode_feedback(_episode("ep-x", used_procedure_ids=("p1",)))
    s.upsert_from_episode(_episode("ep-y", tools_used=("grep",)))

    seen: list[str] = []

    def rename(procs: list[ProcedureRecord]) -> list[ProcedureRecord]:
        seen.extend(p.id for p in procs)
        return [replace(p, name=p.name + "!") for p in procs]

    s.update_procedures(rename)
    after = {p.id: p for p in s.load()}
    assert after["p1"].success_count == 2
    assert after["p1"].name == "p1!"
    assert len(after) == 2 and set(seen) == set(after)


def test_demote_script_does_not_erase_credit_written_after_its_read(tmp_path: Path) -> None:
    pstore = ProceduralMemoryStore(tmp_path / "p.jsonl")
    estore = EpisodicMemoryStore(tmp_path / "e.jsonl")
    pstore.rewrite([
        _proc("dead", "tools:a", s=5, status="active", source_episode_ids=("gone",)),
        _proc("live", "tools:b", s=1),
    ])
    real_load = pstore.load
    stale = real_load()
    # Зачёт другого процесса после того, как скрипт мог прочитать список.
    ep = _episode("ep-z", used_procedure_ids=("live",))
    estore.save(ep)
    pstore.apply_episode_feedback(ep)
    pstore.load = lambda: stale  # type: ignore[method-assign]
    report = select_and_demote(pstore, estore, apply=True)
    pstore.load = real_load  # type: ignore[method-assign]
    after = {p.id: p for p in pstore.load()}
    assert report["demoted"] == ["dead"]
    assert after["dead"].status == "needs_review"
    assert after["live"].success_count == 2


# --- 2. recompute must not undo a deliberate demotion -----------------------------------

def test_recompute_keeps_deliberate_needs_review_and_obsolete(tmp_path: Path) -> None:
    s = ProceduralMemoryStore(tmp_path / "p.jsonl")
    s.rewrite([
        _proc("p-demoted", "tools:a", s=25, f=1, status="needs_review"),
        _proc("p-obsolete", "tools:b", s=25, f=1, status="obsolete"),
        _proc("p-cand", "tools:c", s=25, f=1, status="candidate"),
    ])
    s.recompute_legacy_confidence(dry_run=False)
    after = {p.id: p.status for p in s.load()}
    assert after == {"p-demoted": "needs_review", "p-obsolete": "obsolete", "p-cand": "candidate"}


def test_recompute_still_lowers_and_frees_the_one_one_tie(tmp_path: Path) -> None:
    s = ProceduralMemoryStore(tmp_path / "p.jsonl")
    s.rewrite([
        _proc("tie", "tools:a", s=1, f=1, status="needs_review"),
        _proc("bad", "tools:b", s=1, f=4, status="active"),
        _proc("young", "tools:c", s=1, status="active"),
        _proc("obs-bad", "tools:d", s=0, f=5, status="obsolete"),
    ])
    rep = s.recompute_legacy_confidence(dry_run=False)
    after = {p.id: p.status for p in s.load()}
    assert after == {"tie": "candidate", "bad": "needs_review", "young": "candidate", "obs-bad": "obsolete"}
    assert rep["corrected"] == 3
    again = s.recompute_legacy_confidence(dry_run=False)
    assert again["corrected"] == 0 and again["already_consistent"] == 4
