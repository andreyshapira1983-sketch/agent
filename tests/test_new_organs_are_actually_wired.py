"""Модуль работает — не то же самое, что цикл его зовёт.

УПРЁК ОПЕРАТОРА 2026-08-11, и он попал в дыру метода: «ты сделал файл, а он
подключён? к чему — к воздуху?». Юнит-тест доказывает, что функция считает
верно в изоляции. Он НЕ доказывает, что производственный ход до неё доходит.

Разница не теоретическая. Вся эта сессия — про производителей без потребителей:
`unsupported_deliverables` писался и никем не читался; вердикт уверенности жил в
журнале и не доходил до оператора; `run_identity` не существовал вовсе. Каждый
из них прошёл бы юнит-тесты.

Здесь проверка другая: поднимается НАСТОЯЩИЙ `AgentLoop`, делается ход, и в
журнале ищется событие. Если органа в цепи нет — тест краснеет, сколько бы
зелёных юнит-тестов у модуля ни было.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.policy import PolicyGate
from core.smart_memory import EpisodicMemoryStore
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_ANSWER = (
    "Conclusion: ok. [general-knowledge]\n"
    "Facts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)

#: Запрос СОДЕРЖИТ запрет и требование разделов — иначе часть органов молчит
#: законно, и тест проверял бы пустоту.
_REQUEST = "Не меняй core/loop.py. Отчитайся отдельно разделами."


@pytest.fixture
def journal(tmp_path: Path) -> list[str]:
    """Имена событий одного настоящего хода."""
    registry = ToolRegistry()
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 4),
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[]),
        memory=None,
        max_replan_attempts=1,
        episodic_store=EpisodicMemoryStore(tmp_path / "episodes.jsonl"),
    )
    agent.run(_REQUEST)
    path = tmp_path / "logs" / f"{trace_id}.jsonl"
    return [json.loads(line)["event"]
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("event", [
    "run_identity",          # ребро происхождения прогона
    "completion_contract",   # единицы и покрытие контракта
    "completion_obligation", # сверка ответа с единицами
    "causal_observation",    # наблюдение отклонения — первая ступень конвейера
    "episodic_memory_write",
])
def test_the_organ_is_reached_by_a_real_run(journal: list[str], event: str) -> None:
    """ГЛАВНОЕ: орган в цепи, а не рядом с ней."""
    assert event in journal, (
        f"{event} не появилось в журнале настоящего хода — модуль есть, "
        f"вызова нет. События хода: {sorted(set(journal))}"
    )


def test_no_sensor_failed_during_the_run(journal: list[str]) -> None:
    """Подключён и НЕ ПАДАЕТ — второе не следует из первого.

    Сенсор, который сразу же ловит исключение и пишет о своём сбое, формально
    «в цепи», а фактически мёртв.
    """
    assert "sensor_failed" not in journal, journal


def test_the_observation_carries_what_it_promises(tmp_path: Path) -> None:
    """Событие есть — но несёт ли оно поля, ради которых заведено.

    Пустая полезная нагрузка прошла бы проверку присутствия и не дала бы
    ничего: то же «производитель есть, смысла нет», только на уровне payload.
    """
    registry = ToolRegistry()
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 4),
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[]), memory=None, max_replan_attempts=1,
        episodic_store=EpisodicMemoryStore(tmp_path / "episodes.jsonl"),
    )
    agent.run(_REQUEST)
    path = tmp_path / "logs" / f"{trace_id}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    payload = next(r["payload"] for r in rows if r["event"] == "causal_observation")
    assert payload["defect_signals"], payload
    assert payload["run_id"]
    assert "причина не доказана" in payload["observed_mismatch"]
