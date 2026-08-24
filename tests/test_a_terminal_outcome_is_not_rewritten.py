"""Завершённый исход задачи нельзя переписать другим завершённым.

ИСТОРИЧЕСКИЙ КЛАСС (H-21, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Семейство «конечный автомат принимает невозможный переход» — от Therac-25 до
разборов промышленных контроллеров: состояние меняется путём, которого в
схеме нет, и дальше система рассуждает о мире, которого не было.

ЗАМЕР 2026-08-24, полная матрица терминальных состояний. Единственным
защищённым переходом оказался `mark_running` (его держит проверка захвата).
Всё остальное принималось:

    done      -> failed, cancelled
    failed    -> done,   cancelled
    cancelled -> done,   failed

`failed -> done` — это буквально «ложно доложить успех»: строка, чья работа
провалилась, задним числом становится успешной, и всё, что считает по статусам
(отчёт цикла, полезные циклы, стенд способностей), считает по переписанному.

ДОСТИЖИМОСТЬ, названная честно. В тике settle одна на задачу
(`apply_run_outcome`), поэтому путь узкий: нужно, чтобы исключение возникло
ПОСЛЕ удачного settle и внешний обработчик settle-ил повторно. Но хранилище
предлагает эту возможность КАЖДОМУ вызывающему, включая будущих, и запрет
уместнее там же, где живёт сам переход.

ГРАНИЦЫ. Повтор того же исхода остаётся идемпотентным — на него опираются
вызывающие. `blocked` и `paused` не терминальны: первое ждёт человека, второе
— часов, и оба обязаны оставаться переписываемыми.
"""
from __future__ import annotations

import pathlib

import pytest

from core.task_queue import TaskQueueStore, TerminalOutcomeRewrite


def _store(tmp_path: pathlib.Path) -> TaskQueueStore:
    return TaskQueueStore(path=tmp_path / "q.jsonl")


def _settled(store: TaskQueueStore, state: str):
    task = store.add(kind="auto_run", goal="работа")
    store.mark_running(task.id, owner_pid=1, owner_host="A")
    if state == "done":
        store.mark_done(task.id)
    elif state == "failed":
        store.mark_failed(task.id, error="первая причина")
    elif state == "cancelled":
        store.cancel(task.id)
    return task


@pytest.mark.parametrize(("was", "then"), [
    ("done", "failed"),
    ("done", "cancelled"),
    ("failed", "done"),
    ("failed", "cancelled"),
    ("cancelled", "done"),
    ("cancelled", "failed"),
])
def test_a_terminal_outcome_refuses_a_different_one(tmp_path, was: str, then: str) -> None:
    store = _store(tmp_path)
    task = _settled(store, was)

    # Именно своя ошибка, а не любая: слепое `Exception` прошло бы и на
    # постороннем сбое хранилища, то есть тест краснел бы по чужой причине.
    with pytest.raises(TerminalOutcomeRewrite):
        if then == "done":
            store.mark_done(task.id)
        elif then == "failed":
            store.mark_failed(task.id, error="поздняя причина")
        else:
            store.cancel(task.id)

    assert [t.status for t in store.list()] == [was], (
        f"{was} переписан в {then} — исход задним числом стал другим"
    )


@pytest.mark.parametrize("state", ["done", "failed", "cancelled"])
def test_repeating_the_same_outcome_stays_idempotent(tmp_path, state: str) -> None:
    """Граница: повтор того же исхода — не спор, и вызывающие на нём стоят."""
    store = _store(tmp_path)
    task = _settled(store, state)

    if state == "done":
        store.mark_done(task.id)
    elif state == "failed":
        store.mark_failed(task.id, error="та же причина")
    else:
        store.cancel(task.id)

    assert [t.status for t in store.list()] == [state]


def test_a_blocked_task_still_settles(tmp_path) -> None:
    """Граница: `blocked` ждёт человека и обязан оставаться переписываемым."""
    store = _store(tmp_path)
    task = store.add(kind="auto_run", goal="работа")
    store.mark_running(task.id, owner_pid=1, owner_host="A")
    store.mark_blocked(task.id, reason="нужно одобрение")

    store.mark_done(task.id)

    assert [t.status for t in store.list()] == ["done"]
