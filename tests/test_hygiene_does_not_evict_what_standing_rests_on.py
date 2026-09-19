"""Уборка не выносит эпизоды, на которых стоит зачёт процедур.

Замер, цена и границы: MIR-128 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Оплаченный случай: проход обслуживания вынес 47 эпизодов-опор, и уверенность
0.962 пережила всю свою улику (MIR-058). Починка МЕХАНИЧЕСКАЯ — ни одного
суждения о ценности: эпизод, чей id значится в `source_episode_ids` хоть
одной процедуры, уборке не отдаётся.

Потолок защиты — 60 строк (30 % окна), новейшие из цитируемых: запись MIR-096
уже видела окно, сжатое защитой до ~73 свободных строк, и второй раз этот
урок не покупаем. Переполнение потолка не рвёт систему: улика старейших
уходит, и зачёт честно следует за ней переводом в needs_review
(scripts/demote_unverifiable_procedure_standing.py — другая половина шва).

Большой резольвер консолидации (LLM решает судьбу записей) НЕ строится этим
файлом и остаётся замороженным: кресло судьи над собственной памятью —
MIR-119.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.episodic_hygiene import prune_stale_episodes
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    ProcedureRecord,
)

_OLD = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()


def _old_episode(eid: str, *, days_ago: int = 120) -> EpisodeRecord:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return EpisodeRecord(
        id=eid, question=f"q-{eid}", goal="g", outcome="partial",
        summary="s", created_at=ts,
    )


def test_a_cited_episode_survives_the_prune_an_uncited_twin_does_not(
    tmp_path: Path,
) -> None:
    """Красный свидетель: обе стороны — защита цитируемого, честность к прочим."""
    store = EpisodicMemoryStore(tmp_path / "data" / "episodic_memory.jsonl")
    store.save(_old_episode("ep-cited"))
    store.save(_old_episode("ep-twin"))

    pruned = prune_stale_episodes(
        store, max_age_days=30, keep_ids=frozenset({"ep-cited"}),
    )

    left = {ep.id for ep in store.load()}
    assert "ep-cited" in left, "опора зачёта не отдаётся уборке"
    assert "ep-twin" in pruned and "ep-twin" not in left


def test_protection_is_capped_and_newest_cited_win(tmp_path: Path) -> None:
    """Потолок 60: защита не смеет снова сжать окно (урок MIR-096)."""
    from core.memory_hygiene_commands import cited_episode_ids

    estore = EpisodicMemoryStore(tmp_path / "data" / "episodic_memory.jsonl")
    ids = []
    for i in range(70):
        eid = f"ep-{i:03d}"
        ids.append(eid)
        estore.save(_old_episode(eid, days_ago=200 - i))  # ep-069 новейший
    pstore = ProceduralMemoryStore(tmp_path / "data" / "procedural_memory.jsonl")
    pstore.rewrite([ProcedureRecord(
        name="w", workflow_key="tools:w", trigger_tags=(), steps=("s",),
        source_episode_ids=tuple(ids), success_count=1,
    )])

    protected = cited_episode_ids(pstore, estore)

    assert len(protected) == 60
    assert "ep-069" in protected, "новейший цитируемый защищён"
    assert "ep-000" not in protected, "старейший честно выходит из-под потолка"


def test_the_maintenance_pass_wires_citations_into_the_prune(tmp_path: Path) -> None:
    """Проводка: проход обслуживания сам считает опоры и передаёт их уборке."""
    from core.memory_hygiene_commands import run_maintenance_pass

    estore = EpisodicMemoryStore(tmp_path / "data" / "episodic_memory.jsonl")
    estore.save(_old_episode("ep-cited"))
    estore.save(_old_episode("ep-loose"))
    pstore = ProceduralMemoryStore(tmp_path / "data" / "procedural_memory.jsonl")
    pstore.rewrite([ProcedureRecord(
        name="w", workflow_key="tools:w", trigger_tags=(), steps=("s",),
        source_episode_ids=("ep-cited",), success_count=1,
    )])

    class _Log:
        def log(self, *_a, **_k): ...

    report = run_maintenance_pass(
        log=_Log(), persistent_store=None, episodic_store=estore,
        assumption_store=None, suppressed_reason=None,
        dry_run=False, procedural_store=pstore,
    )

    left = {ep.id for ep in estore.load()}
    assert "ep-cited" in left
    assert "ep-loose" not in left
    assert report.get("episodes_protected_by_citation") == 1


def test_no_procedural_store_means_no_protection_and_no_crash(tmp_path: Path) -> None:
    """Незнание — не приговор: без хранилища процедур уборка идёт как шла."""
    from core.memory_hygiene_commands import run_maintenance_pass

    estore = EpisodicMemoryStore(tmp_path / "data" / "episodic_memory.jsonl")
    estore.save(_old_episode("ep-loose"))

    class _Log:
        def log(self, *_a, **_k): ...

    report = run_maintenance_pass(
        log=_Log(), persistent_store=None, episodic_store=estore,
        assumption_store=None, suppressed_reason=None, dry_run=False,
    )

    assert "error" not in report
    assert {ep.id for ep in estore.load()} == set()
