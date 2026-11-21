"""R1 (2026-08-13): единицы без `#` и единица без тела.

ЖИВОЙ СЛУЧАЙ B2 probe_r1 (`trace_1868e21b`, событие observe дословно):
многострочная вставка дошла до организма без `#` у заголовков и с обрезанным
вопросом S3. Извлекатель знал только markdown-заголовки → units=[],
obligations=[], а видимый голый «S3 — Сравнение» умер молча: achieved, 4/4,
eligible=True — FALSE DONE канала Б.

Инварианты: помеченная единица распознаётся и ПОЛНОЙ строкой без решётки
(двух и более — одиночная строка остаётся прозой); единица, объявленная без
содержания, — ambiguity, а не молчание.
"""
from __future__ import annotations

_B2_OBSERVED = (
    "Три части по probe_r1/config_probe.ini, адресуй каждую отдельно.\n\n"
    "S1 — Ключи\n\nПеречисли все ключи секции [limits].\n\n"
    "S2 — Флаг\n\nКаково значение strict_mode?\n\n"
    "S3 — Сравнение"
)


def test_the_ambiguity_actually_asks(tmp_path) -> None:
    """R1b (живой 407a46c8): `needs_clarification=True` журналировался, а ход
    ехал дальше — у флага не было потребителя; моё «через существующую
    проводку» было завышением. Двусмысленный контракт обязан СПРОСИТЬ до
    планирования."""
    from pathlib import Path

    from core.ids import new_trace_id
    from core.logger import TraceLogger
    from core.loop import AgentLoop
    from core.memory import WorkingMemory
    from core.policy import PolicyGate
    from tests.conftest import FakeLLM, FakePlanner
    from tools.base import ToolRegistry

    registry = ToolRegistry()
    planner = FakePlanner(sources=[])
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry),
        llm=FakeLLM(responses=["x"] * 4),
        logger=TraceLogger(trace_id=new_trace_id(),
                           log_dir=Path(tmp_path) / "logs", verbose=False),
        planner=planner, memory=WorkingMemory(), max_replan_attempts=1,
    )
    answer = agent.run(_B2_OBSERVED)
    assert planner.calls == [], "двусмысленный контракт дошёл до планировщика"
    assert "S3" in answer, answer
