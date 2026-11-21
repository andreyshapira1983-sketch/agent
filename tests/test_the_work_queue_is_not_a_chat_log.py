"""Очередь, кормящая автономный режим, не вправе наполняться разговором.

Background: docs/CODE_NOTES.md, "The work queue was full of conversation".
"""
from __future__ import annotations

from pathlib import Path

from core.task_queue import TaskQueueStore


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
