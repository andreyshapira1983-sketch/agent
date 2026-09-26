"""Запись памяти, выданная одному заходу цели человека, не выдаётся следующему, и её id не едет в его задание."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

GOAL = "Подключи к себе проверенные локальные модели сервера как инструменты"
TARGET = ("Проверенные локальные модели сервера лежат в папке models, и подключить их "
          "как инструменты мешает пустое поле tools в реестре агента")


def _action() -> Any:
    return SimpleNamespace(action="pursue_goal", title="t", severity="medium", priority=1,
                           risk="reversible", grounds="operator_goal", decided_by="goal_first",
                           next_check_at=None, reason="", evidence=())


def _two_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, list[list[str]], list[str]]:
    """Два захода через настоящий campaign_io и настоящую выборку; модель заменена заглушкой рантайма."""
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")
    from app.bootstrap import build_agent
    from core import campaign_io
    from core.campaign_types import CampaignConfig
    from core.memory_door import memory_door_verdict
    from core.memory_policy import MemoryWritePolicy

    agent = build_agent(tmp_path, with_memory=False, with_persistent=True, with_experience=True,
                        episodic_replay=False, durable_writes=frozenset({"episode", "hygiene"}))
    target_id, why = memory_door_verdict(agent.persistent_store, MemoryWritePolicy(), TARGET,
                                         "[НАБЛЮДЕНИЕ, один день]", "logs/run_1.jsonl")
    assert target_id, why
    given: list[list[str]] = []
    goals: list[str] = []

    class _Runtime:
        def __init__(self, agent_: Any, **_kw: Any) -> None:
            self.agent = agent_

        def run(self, config: Any) -> Any:
            goals.append(config.goal)
            self.agent._retrieve_persistent(config.goal)
            ids = [r.id for r in self.agent._last_persistent_records]
            given.append(ids)
            cites = "".join(f" [topic-only:memory:{i}]" for i in ids[:1])
            answer = f"Conclusion: Модели лежат в папке models, поле tools пустое{cites}\nConfidence: low"
            task = SimpleNamespace(task=SimpleNamespace(kind="goal"), status="done", details={"answer": answer})
            return SimpleNamespace(tasks=[task], status="completed", stop_reason="", to_dict=dict,
                                   semantic_result=lambda: ("completed", True))

    monkeypatch.setattr("core.autonomous_runtime.AutonomousRuntime", _Runtime, raising=True)
    for _ in range(2):
        campaign_io._default_execute_action(agent=agent, workspace=tmp_path, action=_action(),
                                            config=CampaignConfig(goal=GOAL, dry_run=False))
    return target_id, given, goals


def test_a_record_given_to_one_pass_is_not_given_again_to_the_next(tmp_path: Path, monkeypatch) -> None:
    """Живой случай 26.09: одна и та же запись mem_cb54… в каждом заходе."""
    target_id, given, _ = _two_passes(tmp_path, monkeypatch)

    assert target_id in given[0]
    assert target_id not in given[1]


def test_the_next_pass_task_carries_the_conclusion_but_no_memory_id(tmp_path: Path, monkeypatch) -> None:
    """Вывод прошлого захода едет дальше, машинная ссылка на запись — нет."""
    target_id, _, goals = _two_passes(tmp_path, monkeypatch)

    assert "Прогресс прошлых заходов" in goals[1]
    assert "поле tools пустое" in goals[1]
    assert target_id not in goals[1]


def test_another_action_does_not_inherit_the_exclusion(tmp_path: Path) -> None:
    """Исключение живёт только в заходе на цель: любое другое действие его снимает."""
    from core.goal_progress import start_pass

    agent = SimpleNamespace(compensation_log=[], memory_given_to_goal=frozenset({"mem_x"}))

    assert start_pass(agent, tmp_path, SimpleNamespace(goal=GOAL, goal_is_self=False),
                      SimpleNamespace(action="observe")) is None
    assert agent.memory_given_to_goal == frozenset()
