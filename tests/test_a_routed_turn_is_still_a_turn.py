"""Ход, отвеченный в обход цикла, обязан остаться в истории сессии.

ЖИВОЙ СЛУЧАЙ 2026-08-12, `trace_d322a875`. Оператор задал подряд шесть
вопросов. Два из них перехватил детерминированный operator-intent маршрут:

    «Найди фактическое место хранения episodic memory…»  -> implementation_plan
    «Проверь реальный конструктор episodic memory store…» -> smart_memory_status

Оба были отвечены посторонней командой. Но хуже другое: их вообще не стало.
Следующий ход показал `turns_visible=1`, планировщик написал «предыдущий шаг не
виден», зависимый эксперимент встал, а на просьбу перечислить все запросы
система насчитала ЧЕТЫРЕ вместо шести.

ПРИЧИНА, замерено offline. Историю сессии пишет ровно одно место — хвост
прогона (`core/loop_run_tail.py`, `memory.record_turn`). Маршрут operator-intent
отвечает и возвращает `True`, `cli/repl.py` делает `continue`, цикл не
запускается — и сообщение оператора не попадает в носитель, из которого
`core/loop_context.py` собирает контекст следующего хода.

НАРУШЕННЫЙ ИНВАРИАНТ: сообщение оператора и данный на него ответ принадлежат
записи сессии независимо от того, какой путь на него ответил. Иначе выбор
маршрута тихо стирает часть разговора.

ГРАНИЦА ЧИНИМОГО. Восстанавливается ФАКТ хода: что спросили и какая команда
ответила. Тело отчёта команды сюда не переносится — обработчики печатают в
stdout, а перехват вывода сломал бы диалоговые команды, ждущие ввода. Этого
достаточно ровно для двух сломанных следствий: следующий планировщик снова
видит вопрос, а перечисление запросов снова полно.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli import intent_bridge
from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.operator_intent import route_operator_intent
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

#: Дословно из живого прогона. `кода/runtime` даёт `/`, «реализации» даёт
#: «реализац» — этой пары хватает, чтобы исследовательский вопрос уехал в план
#: реализации. Никакой модели на этом пути нет: маршрут детерминированный.
_HIJACKED = (
    "Найди фактическое место хранения episodic memory в текущей реализации. "
    "Не угадывай по документации. Дай путь и конкретное evidence из кода/runtime."
)

_ANSWER = (
    "Conclusion: ok. [general-knowledge]\n"
    "Facts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)


@pytest.fixture
def agent(tmp_path: Path) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 6),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[]),
        memory=WorkingMemory(),
        max_replan_attempts=1,
    )


def test_the_hijack_itself_is_still_here() -> None:
    """ПРЕДУСЛОВИЕ, не починка: перехват маршрута — отдельный дефект.

    Этот тест НЕ утверждает, что так правильно. Он закрепляет, что случай
    воспроизводим без модели, и покраснеет, когда маршрутизацию починят
    отдельно — тогда описание выше придётся переписать честно.
    """
    intent = route_operator_intent(_HIJACKED)
    assert intent is not None and intent.kind == "implementation_plan"


def test_a_routed_question_stays_in_the_session(agent: AgentLoop, tmp_path: Path) -> None:
    """ГЛАВНОЕ: перехваченный вопрос не исчезает из истории."""
    agent.run("Это диагностический прогон, ничего не изменяй.")
    before = len(agent.memory.turns)

    assert intent_bridge.handle_conversational_operator_input(
        _HIJACKED, agent, tmp_path
    ) is True

    assert len(agent.memory.turns) == before + 1, (
        "маршрут ответил и стёр ход: следующий планировщик не узнает, что "
        "оператор вообще спрашивал"
    )
    assert "episodic memory" in agent.memory.turns[-1].question


def test_the_next_planner_sees_it(agent: AgentLoop, tmp_path: Path) -> None:
    """КОНЕЦ ЦЕПИ: ход виден там, где собирается контекст следующего.

    Присутствие в списке ничего не стоит, если сборка контекста его не берёт —
    именно это расхождение и наблюдалось живьём.
    """
    intent_bridge.handle_conversational_operator_input(_HIJACKED, agent, tmp_path)
    context = agent.memory.conversation_context(max_turns=5)
    assert "episodic memory" in context, context


def test_the_answering_command_is_named(agent: AgentLoop, tmp_path: Path) -> None:
    """Не «ответ был», а КТО ответил.

    Без имени команды следующий ход снова решит, что вердикт где-то есть, и
    снова застрянет. Записанное имя — это готовое объяснение, почему
    `POSSIBLE/IMPOSSIBLE/UNKNOWN` в разговоре не появился.
    """
    intent_bridge.handle_conversational_operator_input(_HIJACKED, agent, tmp_path)
    turn = agent.memory.turns[-1]
    assert ":implementation-plan" in turn.answer, turn.answer


def test_the_local_reply_carries_its_own_words(agent: AgentLoop) -> None:
    """У локального ответа тело ЕСТЬ — и оно обязано попасть в историю.

    Этот путь отличается от маршрутов команд: текст ответа известен здесь же,
    поэтому подменять его пометкой было бы потерей на ровном месте.
    """
    text = (
        'Не вызывай planner или synthesizer. Reply only with: "ACK-42"'
    )
    assert intent_bridge._handle_local_operator_reply(text, agent) is True
    assert agent.memory.turns[-1].answer.strip() == "ACK-42"


def test_a_message_nobody_answered_is_not_recorded(agent: AgentLoop, tmp_path: Path) -> None:
    """ПРЕДОХРАНИТЕЛЬ: запись появляется только когда ход ДЕЙСТВИТЕЛЬНО отвечен.

    Обычный вопрос проходит маршрут насквозь и попадает в цикл, который запишет
    его сам. Запись здесь же дала бы двойной ход.
    """
    assert intent_bridge.handle_conversational_operator_input(
        "перечисли функции в core/loop.py", agent, tmp_path
    ) is False
    assert agent.memory.turns == []


def test_the_operators_own_falsification(agent: AgentLoop, tmp_path: Path) -> None:
    """КОНЕЦ ЦЕПИ, дословно проваленная живая проверка.

    Оператор задал шесть сообщений и седьмым попросил перечислить их все.
    Система насчитала ЧЕТЫРЕ: два перехваченных маршрутом хода не существовали
    ни для планировщика, ни для восстановления. Здесь считается ровно то же.
    """
    session = [
        "Это диагностический прогон, ничего не изменяй.",
        _HIJACKED,
        ("Проверь реальный конструктор episodic memory store. Можно ли создать "
         "полностью изолированный временный store? Ответ только POSSIBLE, "
         "IMPOSSIBLE или UNKNOWN."),
        "Используй результат предыдущего шага и выполни эксперимент.",
        "Теперь найди в production-коде, кто реально создаёт записи с tag lesson.",
        "Перечисли все утверждения «не найдено», которые ты сделал в этой сессии.",
    ]
    for message in session:
        if not intent_bridge.handle_conversational_operator_input(
            message, agent, tmp_path
        ):
            agent.run(message)

    assert len(agent.memory.turns) == len(session), [
        t.question[:40] for t in agent.memory.turns
    ]
    assert "конструктор episodic memory store" in agent.memory.turns[2].question


def test_no_memory_no_crash(tmp_path: Path) -> None:
    """Память необязательна — её отсутствие не должно ронять маршрут."""
    registry = ToolRegistry()
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER]),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[]), memory=None, max_replan_attempts=1,
    )
    assert intent_bridge.handle_conversational_operator_input(
        _HIJACKED, agent, tmp_path
    ) is True
