"""Что ход выяснил — в долговременную память; что он прочитал по пути — нет.

Замер 2026-09-19 (опыт с библиотекой книг): ответы находились, а по памяти
вспоминалось 2 из 12 — в память шли случайные предложения источников, а
вывод «раздел 2.2 у Judson называется The Division Algorithm» не сохранялся
никогда. См. `core/learned_conclusion.py`.
"""
from __future__ import annotations

from pathlib import Path

from core.approval import AutoApprover
from core.learned_conclusion import conclusion_memory, conclusion_of
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.persistent_memory import PersistentMemoryStore
from core.policy import PolicyGate
from core.smart_memory import EpisodeRecord
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_ANSWER = (
    "Conclusion: Раздел 2.2 в книге Judson «Abstract Algebra» называется "
    "«The Division Algorithm» [file:math_study/library/txt/Judson_AbstractAlgebra.txt].\n"
    "Facts:\n- Заголовок стоит на странице 38 [file:math_study/library/txt/Judson_AbstractAlgebra.txt].\n"
    "Sources:\n1. file:math_study/library/txt/Judson_AbstractAlgebra.txt\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _episode(**kw) -> EpisodeRecord:
    base = {
        "goal": "q", "question": "Как называется раздел 2.2 в книге Judson «Abstract Algebra»?",
        "outcome": "success", "summary": "s", "full_answer": _ANSWER, "completion_state": "achieved",
        "verified_chunks": 2, "unverified_chunks": 0, "tools_used": ["shell_exec", "file_read"],
        "source_labels": ["file:math_study/library/txt/Judson_AbstractAlgebra.txt"],
        "defect_signals": [], "usage_eligible": True,
    }
    base.update(kw)
    return EpisodeRecord(**base)


def test_not_found_and_sourceless_conclusions_are_not_knowledge():
    not_found = _ANSWER.replace("называется «The Division Algorithm»", "найти не удалось")
    assert conclusion_memory(_episode(full_answer=not_found)) is None
    assert conclusion_memory(_episode(source_labels=[])) is None
    assert conclusion_of("просто текст без разделов") == ""


def _loop(workspace: Path) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=[]),
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[]), approval_provider=AutoApprover(default="approve"),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
    )


def test_the_loop_writes_it_through_the_ordinary_memory_door(workspace: Path):
    loop = _loop(workspace)
    loop._remember_conclusion(_episode())
    records = loop.persistent_store.load()
    assert len(records) == 1 and "The Division Algorithm" in records[0].content
    assert {"fact", "conclusion"} <= set(records[0].tags)

    loop._remember_conclusion(_episode())
    assert len(loop.persistent_store.load()) == 1, "тот же вывод дважды — дубль, не второе знание"


def test_a_corrected_conclusion_replaces_the_old_one(workspace: Path):
    """Замер 2026-09-19 (четвёртый прогон): в памяти остался «страница 326» при
    верной 327. Исправленный вывод почти дословно совпадает со старым, и защита
    от дублей отвергла бы его — ошибка закреплялась бы навсегда."""
    loop = _loop(workspace)
    loop._remember_conclusion(_episode())
    wrong = _ANSWER.replace("«The Division Algorithm»", "«The Euclidean Algorithm»")
    loop._remember_conclusion(_episode(full_answer=wrong))
    active = loop.persistent_store.load()
    assert len(active) == 1 and "Euclidean" in active[0].content, "новый вывод заменил старый"
    archived = loop.persistent_store.load_archive()
    assert len(archived) == 1 and "Division" in archived[0].content, "старый ушёл в архив, не стёрт"

    loop._remember_conclusion(_episode(full_answer=wrong))
    assert len(loop.persistent_store.load()) == 1
    assert len(loop.persistent_store.load_archive()) == 1, "тот же вывод повторно — ничего не пишется"
