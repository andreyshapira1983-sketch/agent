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

ЗАМЕРЕНО 2026-08-11: потребителя нет. Тест ниже был поставлен НАРОЧНО, чтобы
покраснеть в день, когда потребитель появится, и заставить переписать это
описание честно.

ОН ПОКРАСНЕЛ 2026-08-15, и вот честная версия. Потребитель есть:
`core/causal_store.py` хранит наблюдение, `app/bootstrap.py` его заводит,
`loop_memory_write` пишет туда и добавляет `occurrences` в журнал. То есть
доказанный участок вырос:

    AgentLoop → сенсор → событие → журнал → ХРАНИЛИЩЕ (переживает ход)

Чего по-прежнему НЕТ, и это следующая честная граница: ПОДЪЁМА. Ни один
производственный путь не строит `CausalClaim` из наблюдения, не выдвигает
конкурирующих объяснений и не ставит вмешательства, поэтому `state_of` в
производстве не поднимается выше `OBSERVED`. Ступени EXPLAINED, ATTRIBUTED,
GENERALIZED и LESSON существуют, проверяются юнит-тестами и в живом ходу не
достигаются никем.

ОН ПОКРАСНЕЛ СНОВА В ТОТ ЖЕ ДЕНЬ. Подъём построен (`core/causal_climb.py`) и у
него есть водитель — `cli/commands_causal.py`, команда `:causal`. Оператор
подаёт конкурирующие гипотезы, лестница отвечает, чего не хватает. Живой замер:
две гипотезы выдвинуты, состояние осталось OBSERVED, причина — «соперники не
разобраны».

Граница сдвинулась ещё раз, и вот она честно: поднимается ОПЕРАТОР, не агент.
Ни один автоматический путь не выдвигает гипотез о себе — замерено 2026-08-15
на задании про документацию Python: агент нашёл источник и не превратил его в
вопрос к себе. Дать модели сочинять гипотезы всё равно значило бы получить
ATTRIBUTED со смыслом «модель уверена».

Тест ниже стережёт уже эту границу и покраснеет в день, когда гипотезы начнёт
выдвигать сам агент.
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


def test_the_agent_itself_proposes_no_hypotheses() -> None:
    """ИЗВЕСТНАЯ ГРАНИЦА: поднимается оператор, не агент.

    Подъём построен и у него есть водитель — команда `:causal`. Но гипотезы
    выдвигает человек: ни один автоматический путь не зовёт
    `propose_explanation`. Пока это так, `ATTRIBUTED` означает «оператор
    доказал», а не «модель уверена», и это единственная честная версия.

    Когда агент начнёт выдвигать гипотезы сам, тест покраснеет.
    """
    import re

    root = Path(__file__).parent.parent
    # Строители утверждений, а не любые упоминания: хранение наблюдения —
    # это первая ступень, а подъёмом было бы создание `CausalClaim`.
    # Автоматические выдвиженцы гипотез, а не операторская команда.
    pattern = re.compile(r"propose_explanation\(")
    owners = {"causal_lesson.py", "causal_climb.py", "commands_causal.py"}
    consumers = [
        str(path.relative_to(root))
        for area in ("core", "cli", "app", "api", "tools")
        for path in (root / area).rglob("*.py")
        if path.name not in owners
        and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not consumers, (
        "гипотезы выдвигает уже не только оператор — описание этого "
        f"агент начал выдвигать гипотезы сам: {consumers}"
    )
