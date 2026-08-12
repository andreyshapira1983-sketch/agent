"""Датчик доходит до журнала. НЕ «орган подключён к поведению».

УПРЁК ОПЕРАТОРА 2026-08-11, первый: «ты сделал файл — а он подключён? к чему,
к воздуху?». Юнит-тест доказывает, что функция считает верно в изоляции, и
НИЧЕГО не говорит о том, зовёт ли её производственный ход.

УПРЁК ВТОРОЙ, в тот же день, и он про этот файл: прежнее имя
`test_new_organs_are_actually_wired` завышало доказанное. Здесь доказан участок

    AgentLoop → сенсор → событие → журнал

и только он. Полная цепь была бы иной:

    производитель → наблюдение → ПОТРЕБИТЕЛЬ → состояние конвейера
                  → LESSON → извлечение → изменение будущего плана

ЗАМЕРЕНО 2026-08-11: потребителя нет. `causal_observation` не читает никто;
`CausalClaim`, `state_of`, `is_lesson` в производстве не вызываются. То есть
датчик жив, а слушателя у него пока не существует — ровно тот класс «producer
без consumer», который эта сессия и разбирает, только теперь мой собственный.

Различие закреплено тестом ниже НАРОЧНО: если завтра потребитель появится, он
покраснеет и заставит переписать это описание честно. Иначе зелёный тест через
неделю снова станет красивой ложью о состоянии системы.
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


def test_the_consumer_is_still_absent() -> None:
    """ИЗВЕСТНЫЙ ПРОБЕЛ, закреплённый нарочно: слушателя у датчика нет.

    Наблюдение пишется в журнал и НИКЕМ не читается. Ни `CausalClaim`, ни
    `state_of`, ни `is_lesson` не вызываются производственным кодом — конвейер
    подключён только первой ступенью.

    Когда потребитель появится, этот тест покраснеет. Это и есть его работа:
    заставить обновить описание вместо того, чтобы зелень молчаливо
    рассказывала о цепи, которой нет.
    """
    import re

    root = Path(__file__).parent.parent
    pattern = re.compile(r"causal_observation|is_lesson|CausalClaim")
    owners = {"causal_lesson.py", "loop_memory_write.py"}
    consumers = [
        str(path.relative_to(root))
        for area in ("core", "cli", "app", "api", "tools")
        for path in (root / area).rglob("*.py")
        if path.name not in owners
        and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not consumers, (
        "у наблюдения появился потребитель — цепь выросла, и описание этого "
        f"файла больше не верно: {consumers}"
    )
