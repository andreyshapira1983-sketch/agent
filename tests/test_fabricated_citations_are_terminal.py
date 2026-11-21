"""R4 и R5 — два решения оператора 2026-08-13.

R4: «сфабрикованные цитаты терминальны». Живой случай R-E1 ход 2
(`run_7b1b2caf`): ответ «36 × 3 = 108» с двумя цитатами [dialogue:previous]
на диалог, которого не существовало, ушёл оператору как есть — усечение не
добрало кусков, категориальная ветка спала за флагом, а для фабрикации ветки
не было вовсе. Определение одно и уже существует: fabricated ==
`cited_but_unmatched` (`core/evidence_support.py`). Фабрикация — свойство
текста, не эвристика: рубеж срабатывает независимо от режима раскатки.

R5: «включай referent_resolver». Орган построен, выключен переменной, которую
никто не выставлял; тот же живой ход показал цену: анафора «результат
предыдущего шага» при ПУСТОЙ истории пошла в планирование и взяла чужие 36 из
опыта. Включение + недостающее ребро: анафора без антецедента — это вопрос
оператору ДО планирования, через существующие ворота уточнений.
"""
from __future__ import annotations

from pathlib import Path

from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry


def test_a_present_antecedent_is_not_questioned(tmp_path: Path) -> None:
    """ПРЕДОХРАНИТЕЛЬ R5: при живой истории уточнение не выпрашивается."""
    _ANSWER = (
        "Conclusion: ok. [general-knowledge]\n"
        "Facts:\n- ok [general-knowledge]\n"
        "Sources:\n1. general-knowledge - general-knowledge\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    registry = ToolRegistry()
    planner = FakePlanner(sources=[])
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 8),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs",
                           verbose=False),
        planner=planner, memory=WorkingMemory(), max_replan_attempts=1,
    )
    agent.run("Посчитай сумму qty на полке B2.")
    agent.run("Раздели результат предыдущего шага на два.")
    assert len(planner.calls) == 2, "уточнение выпрошено при живом антецеденте"


def test_a_task_clause_is_not_supplied_text() -> None:
    """Хвост формулировки задачи — не принесённый текст; многострочный или в кавычках — принесённый.

    Критика-без-объекта съедала содержательную задачу (без роли, инструментов, памяти)."""
    from core.referent_resolver import ReferentResolver, is_local_critique_eligible

    r = ReferentResolver()
    kw = {"prior_turns": (), "artifacts": (), "file_hint": None,
          "current_session_id": "s", "current_turn_id": "t"}
    task = r.resolve("проанализируй архитектуру проекта и предложи план "
                     "рефакторинга слоя памяти с обоснованием", **kw)
    assert not is_local_critique_eligible(task)

    for brought in (
        ("проанализируй этот текст:\nПервая строка принесённого отчёта о продажах.\n"
         "Вторая строка выводов о падении спроса."),
        'проанализируй этот текст:\n"Первая строка.\nВторая строка выводов."',
    ):
        assert is_local_critique_eligible(r.resolve(brought, **kw)), brought
