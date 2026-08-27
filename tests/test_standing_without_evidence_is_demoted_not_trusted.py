"""Зачёт, чья улика вынесена уборкой, не смеет дальше править планированием.

Замер и решение: MIR-058 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Живой случай: `tools:file_read` — уверенность 0.962, статус active,
31 ссылка на эпизоды, ВСЕ 31 отсутствуют. Самая уверенная процедура опирается
на ноль строк улики: временнáя коэрция `witnessed_then → assumed_now`.

Решение — не стирание и не переоценка: счётчики остаются как есть (журнал
честен: «было начислено»), меняется только СТАТУС — needs_review, который
фильтр подбора уже отсекает (core/smart_memory.py: search_with_report).
Обратимо: будущие проверяемые завершения могут вернуть active по обычному
жизненному циклу.
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    ProcedureRecord,
)
from scripts.demote_unverifiable_procedure_standing import select_and_demote


def _proc(pid: str, *, refs: tuple[str, ...], success: int = 5,
          status: str = "active") -> ProcedureRecord:
    return ProcedureRecord(
        id=pid, name=f"w-{pid}", workflow_key=f"tools:{pid}",
        trigger_tags=(), steps=("s",), source_episode_ids=refs,
        success_count=success, failure_count=0, confidence=0.9, status=status,
    )


def _stores(tmp_path: Path, procs, episode_ids=()) -> tuple:
    pstore = ProceduralMemoryStore(tmp_path / "data" / "procedural_memory.jsonl")
    pstore.rewrite(list(procs))
    estore = EpisodicMemoryStore(tmp_path / "data" / "episodic_memory.jsonl")
    for eid in episode_ids:
        estore.save(EpisodeRecord(
            id=eid, question="q", goal="g", outcome="success", summary="s"))
    return pstore, estore


def test_all_evidence_gone_and_nonzero_standing_is_demoted(tmp_path: Path) -> None:
    """Красный свидетель: правило отбора — обе части обязательны."""
    pstore, estore = _stores(tmp_path, [
        _proc("gone", refs=("ep-a", "ep-b")),          # улика вся вынесена
        _proc("alive", refs=("ep-live",)),             # одна опора жива
        _proc("zero", refs=("ep-c",), success=0),      # зачёта нет — не трогаем
        _proc("rev", refs=("ep-d",), status="needs_review"),  # уже переведена
    ], episode_ids=("ep-live",))

    report = select_and_demote(pstore, estore, apply=True)

    by_id = {p.id: p for p in pstore.load()}
    assert by_id["gone"].status == "needs_review"
    assert by_id["alive"].status == "active"
    assert by_id["zero"].status == "active"
    assert by_id["rev"].status == "needs_review"
    assert report["demoted"] == ["gone"]


def test_counters_survive_the_demotion_untouched(tmp_path: Path) -> None:
    """Журнал честен: «было начислено» не переписывается, меняется статус."""
    pstore, estore = _stores(tmp_path, [_proc("gone", refs=("ep-a",), success=24)])

    select_and_demote(pstore, estore, apply=True)

    row = pstore.load()[0]
    assert row.success_count == 24
    assert row.confidence == 0.9


def test_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    pstore, estore = _stores(tmp_path, [_proc("gone", refs=("ep-a",))])

    report = select_and_demote(pstore, estore, apply=False)

    assert report["demoted"] == ["gone"]
    assert pstore.load()[0].status == "active"


def test_a_needs_review_procedure_is_not_served_to_the_planner(tmp_path: Path) -> None:
    """Дверь, ради которой перевод статуса: подбор её уже отсекает."""
    pstore, _ = _stores(tmp_path, [
        _proc("gone", refs=("ep-a",), status="needs_review"),
    ])

    result = pstore.search_with_report("w-gone tools", limit=3)

    served = [p.id for p in getattr(result, "procedures", result[0] if isinstance(result, tuple) else [])]
    assert "gone" not in served
