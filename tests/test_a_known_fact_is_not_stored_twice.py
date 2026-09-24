"""Известный факт не записывается второй раз под другими словами.

Замер 2026-09-23 на живой памяти: двенадцать записей об одной и той же
теореме Нётер у Тонга, одиннадцать о лексическом анализе у Могенсена, семь
разборов одного случая. Дубль-сторож (похожесть текста не ниже 0.85)
пропустил все: одно и то же, написанное разными словами, до 0.85 не дотягивает.

Лечение по Mem0 (arXiv 2504.19413, Алгоритм 1): похожие выводы отбираются,
решает модель — ADD, UPDATE, DELETE, NOOP. UPDATE СЛИВАЕТ прежний и новый
вывод под прежним вопросом, как в исходниках Mem0: на живом прогоне модель
четыре раза из двадцати выбрала UPDATE для близких соседей (инвариант и
псевдокод Дейкстры), и замена стёрла бы знание, а слияние — нет.
Всё обратимо (архив), любой сбой модели — ADD. См. `core/memory_consolidation.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory_consolidation import similar_conclusions
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.policy import PolicyGate
from core.smart_memory import EpisodeRecord
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_BOOK = "file:knowledge_library/physics/txt/Tong_ClassicalDynamics.txt"
# Без термина в скобках или кавычках: структурный ключ здесь молчит, как на
# живых двенадцати записях.
_QUESTION = "Найди в книге Тонга формулировку теоремы Нётер и объясни её"


def _answer(conclusion: str) -> str:
    return f"Conclusion: {conclusion}\nSources:\n1. {_BOOK}\nConfidence: high\n"


def _episode(conclusion: str, question: str = _QUESTION) -> EpisodeRecord:
    return EpisodeRecord(
        goal="q", question=question, outcome="success", summary="s",
        full_answer=_answer(conclusion), completion_state="achieved",
        verified_chunks=2, unverified_chunks=0, tools_used=["file_read"],
        source_labels=[_BOOK], defect_signals=[], usage_eligible=True,
    )


FIRST = ("Теорема Нётер у Тонга, раздел 2.4.1: каждой непрерывной симметрии "
         "лагранжиана соответствует сохраняющаяся величина.")
RESTATED = ("В разделе 2.4.1 книги Тонга сказано, что у всякой непрерывной "
            "симметрии лагранжиана есть сохраняющийся заряд.")


def _loop(workspace: Path, fake: FakeLLM) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=fake,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[]), approval_provider=AutoApprover(default="approve"),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
    )


def _stored_first(workspace: Path) -> tuple[AgentLoop, FakeLLM, str]:
    fake = FakeLLM()
    loop = _loop(workspace, fake)
    loop._remember_conclusion(_episode(FIRST))
    records = loop.persistent_store.load()
    assert len(records) == 1
    return loop, fake, records[0].id


def _say(fake: FakeLLM, **answer) -> None:
    fake.responses.append(json.dumps({"target": None, "merged": None, "title": None, **answer}))


def test_a_short_question_on_an_empty_memory_costs_no_model_call(workspace: Path) -> None:
    _, fake, _id = _stored_first(workspace)
    assert fake.calls == [], "на пустой памяти сверять не с чем — модель не зовётся"


def test_a_restated_fact_is_not_stored_again(workspace: Path) -> None:
    loop, fake, first_id = _stored_first(workspace)
    _say(fake, operation="NOOP", target=first_id)

    loop._remember_conclusion(_episode(RESTATED))

    records = loop.persistent_store.load()
    assert [r.id for r in records] == [first_id], "тот же факт другими словами лёг вторым"
    assert first_id in fake.calls[-1]["user"], "модели не показали прежний вывод"


def test_a_complementary_restatement_is_merged_under_the_old_question(workspace: Path) -> None:
    """UPDATE сливает, а не заменяет (так в исходниках Mem0): знание обоих остаётся."""
    loop, fake, first_id = _stored_first(workspace)
    merged = FIRST + " Пример: сдвиг даёт сохранение импульса."
    _say(fake, operation="UPDATE", target=first_id, merged=merged)

    loop._remember_conclusion(_episode("Пример у Тонга: инвариантность относительно сдвига даёт сохранение импульса.",
                                       question="Какой пример теоремы Нётер приводит Тонг"))

    records = loop.persistent_store.load()
    assert len(records) == 1
    assert records[0].content.startswith(f"Вопрос: {_QUESTION}\n"), "ключ прежней записи поплыл"
    assert f"Вывод: {merged}" in records[0].content
    assert first_id in {r.id for r in loop.persistent_store.load_archive()}, "прежний не в архиве"


def test_an_update_without_merged_text_keeps_both(workspace: Path) -> None:
    """Без слитого текста заменять нечем — обе записи остаются."""
    loop, fake, first_id = _stored_first(workspace)
    _say(fake, operation="UPDATE", target=first_id)

    loop._remember_conclusion(_episode(RESTATED))

    assert len(loop.persistent_store.load()) == 2


def test_a_contradiction_archives_the_old_conclusion(workspace: Path) -> None:
    loop, fake, first_id = _stored_first(workspace)
    _say(fake, operation="DELETE", target=first_id)

    loop._remember_conclusion(_episode("Теорема Нётер у Тонга сформулирована в разделе 3.1, а не 2.4.1."))

    records = loop.persistent_store.load()
    assert len(records) == 1 and "3.1" in records[0].content
    assert first_id in {r.id for r in loop.persistent_store.load_archive()}


def test_new_knowledge_is_added(workspace: Path) -> None:
    loop, fake, _first = _stored_first(workspace)
    _say(fake, operation="ADD")

    loop._remember_conclusion(_episode("Сохранение энергии у Тонга следует из однородности времени."))

    assert len(loop.persistent_store.load()) == 2


def test_a_model_failure_never_loses_a_conclusion(workspace: Path) -> None:
    """Неразборчивый ответ и ссылка на несуществующую запись — ADD, как было."""
    loop, fake, _first = _stored_first(workspace)
    fake.responses.append("не могу ответить")
    loop._remember_conclusion(_episode(RESTATED))
    assert len(loop.persistent_store.load()) == 2

    _say(fake, operation="NOOP", target="mem_nonexistent")
    loop._remember_conclusion(_episode("Теорема Нётер связывает симметрию и закон сохранения у Тонга."))
    assert len(loop.persistent_store.load()) == 3


def test_a_letter_as_question_gets_a_short_title(workspace: Path) -> None:
    """ReasoningBank: в память — заголовок, а не сырое письмо на 300 знаков."""
    fake = FakeLLM()
    loop = _loop(workspace, fake)
    letter = ("Claude. Я прочитал твой разбор и хочу, чтобы ты сам проверил: " * 6) + _QUESTION
    _say(fake, operation="ADD", title="Как формулируется теорема Нётер у Тонга?")

    loop._remember_conclusion(_episode(FIRST, question=letter))

    record = loop.persistent_store.load()[0]
    assert record.content.startswith("Вопрос: Как формулируется теорема Нётер у Тонга?\n")
    assert "Claude. Я прочитал" not in record.content


def test_only_conclusions_are_offered_to_the_model() -> None:
    """Сверяется вывод с выводами; урок с теми же словами — не кандидат."""
    lesson = MemoryRecord(content="Теорема Нётер у Тонга: урок о симметрии.", type="semantic",
                          tags=["fact", "lesson"], owner="self", source="agent-auto")
    conclusion = MemoryRecord(content=f"Вопрос: {_QUESTION}\nВывод: {FIRST}", type="semantic",
                              tags=["fact", "conclusion"], owner="self", source="agent-auto")
    got = similar_conclusions(f"Вопрос: {_QUESTION}\nВывод: {RESTATED}", [lesson, conclusion])
    assert got == [conclusion]
