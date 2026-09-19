"""Возобновление имеет не только нижнюю границу, но и верхнюю.

ЧТО ЗАМЕРЕНО. В живой очереди на 2026-08-22 лежали 14 строк
`resume_checkpoint`, все остановленные `budget_exhausted`: самая старая от
2026-07-30, самая свежая от 2026-08-15. Среди целей — «Answer the question:
привет» и обрывки чатовых заданий оператора. Лестница возобновления
(`reactivate_paused_checkpoints`) имела ТОЛЬКО нижнюю границу — cooldown 60
минут — и порядок «сначала самые старые», батч 3 за проход. Значит первые
~5 тиков безнадзорной недели ушли бы на переответы июльского чата: с расходом
бюджета, с записью «работа сделана» и вперёд автономного выбора, потому что
шаг очереди в тике идёт ДО шага самостоятельного производителя.

ПОЧЕМУ ЭТО ГРАНИЦА, А НЕ ФИЛЬТР ПО СОДЕРЖИМОМУ. Судить о ценности цели по её
тексту — гадание. Судить по возрасту — факт: контрольная точка старше потолка
описывает мир, которого больше нет (файлы изменились, бюджетное окно другое,
разговор, породивший вопрос, закончился недели назад).

ЧТО ПРОИСХОДИТ СО СТАРОЙ СТРОКОЙ. Она не остаётся paused — это и была ошибка:
строка, которая никогда не побежит, продолжала рекламировать себя резюмируемой
в `summary()`. Она становится терминальной с названной причиной, ровно как уже
делает соседняя ветка про исчерпанные попытки.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.task_queue import RuntimeTask, TaskQueueStore

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _parked(store: TaskQueueStore, *, goal: str, age_hours: float) -> str:
    task = store.add_paused_checkpoint(
        goal=goal, report={"stop_reason": "budget_exhausted"}
    )
    stamp = (NOW - timedelta(hours=age_hours)).isoformat()
    # `with_updates` намеренно переставляет `updated_at` на «сейчас», поэтому
    # возраст ставится через сырую запись — публичного пути для этого нет.
    rows = []
    for row in store.list():
        data = row.to_dict()
        if data["id"] == task.id:
            data["updated_at"] = stamp
        rows.append(RuntimeTask.from_dict(data))
    store._save_unlocked(rows)
    return task.id


def _by_id(store: TaskQueueStore, task_id: str):
    return next(t for t in store.list() if t.id == task_id)


def test_a_month_old_checkpoint_is_not_resumed(tmp_path: Path) -> None:
    store = TaskQueueStore(path=tmp_path / "runtime_tasks.jsonl")
    old = _parked(store, goal="Answer the question: привет", age_hours=24 * 30)

    store.reactivate_paused_checkpoints(now=NOW)

    assert _by_id(store, old).status != "pending", (
        "июльская реплика из чата вернулась в очередь и съест начало недели "
        "вперёд работы, которую агент выбрал бы сам"
    )


def test_the_stale_row_becomes_terminal_with_a_named_reason(tmp_path: Path) -> None:
    """Не paused: строка, которая никогда не побежит, не должна рекламировать
    себя резюмируемой."""
    store = TaskQueueStore(path=tmp_path / "runtime_tasks.jsonl")
    old = _parked(store, goal="Answer the question: привет", age_hours=24 * 30)

    store.reactivate_paused_checkpoints(now=NOW)

    row = _by_id(store, old)
    assert row.status in {"failed", "cancelled"}, row.status
    assert "stale" in str(row.last_error or "").lower(), row.last_error


def test_a_fresh_checkpoint_is_still_resumed(tmp_path: Path) -> None:
    """Граница с другой стороны: починка бюджетных пауз не должна превратиться
    в свою противоположность."""
    store = TaskQueueStore(path=tmp_path / "runtime_tasks.jsonl")
    fresh = _parked(store, goal="доделать разбор реестра", age_hours=2)

    store.reactivate_paused_checkpoints(now=NOW)

    assert _by_id(store, fresh).status == "pending", (
        "свежая бюджетная пауза не вернулась — потолок съел то, ради чего "
        "лестницу и строили"
    )


def test_the_cooldown_still_holds(tmp_path: Path) -> None:
    """И нижняя граница на месте: единственная попытка не тратится, пока окно
    ещё сухое."""
    store = TaskQueueStore(path=tmp_path / "runtime_tasks.jsonl")
    just_now = _parked(store, goal="только что остановлено", age_hours=0.1)

    store.reactivate_paused_checkpoints(now=NOW)

    assert _by_id(store, just_now).status == "paused"
