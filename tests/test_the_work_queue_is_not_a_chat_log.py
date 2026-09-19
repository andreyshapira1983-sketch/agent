"""Очередь, кормящая автономный режим, не вправе наполняться разговором.

Background: docs/CODE_NOTES.md, "The work queue was full of conversation".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.task_queue import TaskQueueStore, checkpoint_is_resumable_work

#: Дословно из очереди 2026-08-15 — 14 приостановленных «задач».
_MEASURED_CHAT_GOALS = [
    "Answer the question: привет",
    "Answer the question: что ты чувствуешь когда ты неправ и тебе стыдно?",
    "Answer the question: >>[STRA] strategy_classified  strategy=general_question",
]


def test_a_live_conversation_is_not_queued_as_work():
    """Оператор сидит у экрана: прерванную реплику он наберёт заново, а
    очередь кормит непригляданный режим.
    """
    assert checkpoint_is_resumable_work("repl") is False


def test_an_absent_path_is_treated_as_conversation():
    """Умолчание у самого цикла — `repl` (`core/loop_init.py`). Если поле не
    доехало, значит никто его не менял, а менял бы рантайм.
    """
    assert checkpoint_is_resumable_work(None) is False
    assert checkpoint_is_resumable_work("") is False


@pytest.mark.parametrize("path", ["runtime", "daemon", "self_apply", "cli"])
def test_unattended_work_is_still_queued(path: str):
    """Улов не отдан: прерванный `:auto-run` возобновлять НАДО — за ним никто
    не следит, и он единственный, ради кого очередь заведена.
    """
    assert checkpoint_is_resumable_work(path) is True


def test_an_unknown_path_is_kept_rather_than_dropped():
    """Новый путь появится — и очередь скорее сохранит лишнее, чем потеряет
    работу. Ошибка в эту сторону стоит одной лишней строки; в обратную —
    молча потерянной задачи.
    """
    assert checkpoint_is_resumable_work("some_future_lane") is True


@pytest.mark.parametrize("goal", _MEASURED_CHAT_GOALS)
def test_the_decision_never_looks_at_the_words(goal: str):
    """Различие структурное. Судить по тексту значило бы гадать, чем «привет»
    отличается от «почини X», — тот самый разбор по словарю, который здесь
    всюду проигрывает структурному факту.
    """
    assert checkpoint_is_resumable_work("repl") is False
    assert checkpoint_is_resumable_work("runtime") is True


def test_the_queue_still_accepts_a_checkpoint_when_asked(tmp_path: Path):
    """Сам механизм парковки не тронут — изменилось только, кто до него
    доходит. Иначе прерванная работа перестала бы возобновляться.
    """
    store = TaskQueueStore(tmp_path / "runtime_tasks.jsonl")

    task = store.add_paused_checkpoint(
        goal="project health sweep", report={"stop_reason": "budget_exhausted"},
    )

    assert task.kind == "resume_checkpoint"
    assert task.status == "paused"


def _fake_agent(tmp_path: Path, gateway_path: str):
    """Минимум, который читает `_persist_resumable_budget_stop`."""
    class _Log:
        # Список заводится в `__init__`, а не на классе: общий изменяемый
        # атрибут протёк бы между двумя тестами этого файла.
        trace_id = "trace_test"
        log_dir = tmp_path / "logs"

        def __init__(self):
            self.events: list = []

        def log(self, event, payload=None):
            self.events.append((event, payload or {}))

    class _Agent:
        log = _Log()

    agent = _Agent()
    agent.gateway_path = gateway_path
    agent.log.log_dir.mkdir(parents=True, exist_ok=True)
    return agent


def _drive(agent, tmp_path: Path):
    from app.budget_guard import _persist_resumable_budget_stop
    from core.model_usage import ModelBudgetExceeded

    _persist_resumable_budget_stop(
        agent,
        workspace=tmp_path,
        user_question="привет",
        file_hint=None,
        blocked=ModelBudgetExceeded("limit reached"),
    )
    store = TaskQueueStore(tmp_path / "data" / "runtime_tasks.jsonl")
    return store.list()


def test_the_guard_itself_keeps_a_chat_turn_out_of_the_queue(tmp_path: Path):
    """Живой путь целиком, а не только предикат.

    Утром 2026-08-15 семь зелёных тестов сопровождали правку, которая на живом
    пути не сработала: значение доезжало до черновика и терялось на печати. Тот
    урок стоит одного теста, который дёргает настоящую функцию.
    """
    agent = _fake_agent(tmp_path, "repl")

    tasks = _drive(agent, tmp_path)

    assert tasks == []
    assert any(e == "resumable_task_not_queued" for e, _ in agent.log.events), (
        "ход отброшен молча — оператор не узнает, почему возобновлять нечего"
    )


def test_the_guard_still_queues_interrupted_unattended_work(tmp_path: Path):
    """Ломка наоборот: отсекая разговор, не отсечь работу."""
    agent = _fake_agent(tmp_path, "runtime")

    tasks = _drive(agent, tmp_path)

    assert len(tasks) == 1
    assert tasks[0].kind == "resume_checkpoint"
    # Отчёт обязан нести, ЧТО возобновлять и почему остановились: без trace_id
    # припаркованная строка не соединяется ни с одной контрольной точкой.
    assert tasks[0].last_report["trace_id"] == "trace_test"
    assert tasks[0].last_report["stop_reason"] == "budget_exhausted"
