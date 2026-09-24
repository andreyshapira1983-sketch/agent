"""Неизменный факт пишется в журнал один раз, а не на каждом ходу (24.09).

Замер живых хранилищ сервера: data/assumptions.jsonl — 428 из 440 строк
повторы («the user expects a russian language response» ×128);
data/self_repair_log.jsonl — 396 из 408, одна строка «дефект без задачи
пропущен» ×102. Оба журнала никто не пересчитывает: один отвечает «что
предполагалось в последний раз», другой — сводка для человека. Повтор того же
пишется раз в сутки (repeat_interval Alertmanager), изменение — сразу.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.assumption_registry import Assumption, AssumptionStore

_T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _lang(run: str, at: datetime, verified: bool | None = None) -> Assumption:
    return Assumption(run_id=run, text="The user expects a Russian-language response.",
                      category="language", source="profile", verified=verified, created_at=at)


def test_the_same_assumption_in_every_run_is_stored_once_a_day(tmp_path: Path) -> None:
    store = AssumptionStore(tmp_path / "data" / "assumptions.jsonl")
    written = [store.save_many([_lang(f"run{i}", _T0 + timedelta(minutes=i))]) for i in range(5)]
    assert written == [1, 0, 0, 0, 0]
    assert store.save_many([_lang("run6", _T0 + timedelta(minutes=6), verified=True)]) == 1, \
        "a changed verdict is new information and is written at once"
    assert store.save_many([_lang("run7", _T0 + timedelta(hours=25))]) == 1, \
        "an unchanged fact is refreshed after a day, so :assumptions still shows it"
    assert len(store.load_recent(50)) == 3


def _skip_setup(tmp_path: Path, title: str = "дефект без задачи") -> None:
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry
    from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked

    path = tmp_path / DEFAULT_ISSUE_PATH
    if not path.exists():
        SelfImprovementIssueRegistry(path).upsert_failure(title, "2026-09-23T01:00:00+00:00")
    rows = read_state_jsonl_unlocked(path)
    for row in rows:
        row["suggested_next_action"] = ""
        row["title"] = title
    rewrite_state_jsonl_unlocked(path, rows)


def test_a_skipped_defect_is_reported_once_not_every_cycle(tmp_path: Path) -> None:
    from core.patch_route import LOG_RELPATH, defect_goal

    _skip_setup(tmp_path)
    for _ in range(5):
        assert defect_goal(tmp_path) is None
    log = (tmp_path / LOG_RELPATH).read_text(encoding="utf-8")
    assert log.count("defect_without_a_task_skipped") == 1

    _skip_setup(tmp_path, title="дефект без задачи, заголовок уточнён")
    defect_goal(tmp_path)
    log = (tmp_path / LOG_RELPATH).read_text(encoding="utf-8")
    assert log.count("defect_without_a_task_skipped") == 2, "a changed defect is reported again"
