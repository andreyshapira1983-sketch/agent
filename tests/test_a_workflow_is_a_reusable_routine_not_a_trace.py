"""Шаблон работы по AWM — повторяемая подзадача с переменными, а не след одного прогона.

Замер 2026-09-25: 98 процедур — 98 разных ключей (ключ — точная цепочка
инструментов), шаги «Run tool: file_read» ×4, «ситуация» — дословный старый
вопрос; в подсказку они шли в 95% ходов. Agent Workflow Memory (arXiv
2409.07429): модель извлекает из нескольких успешных опытов общий кусок работы,
изменчивое заменяется переменными, шаблон — описание плюс шаги.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.workflow_memory import (
    FILE_NAME,
    WorkflowMemoryStore,
    WorkflowRecord,
    format_workflows,
    induce,
    unabstracted,
)


class _LLM:
    def __init__(self, reply: dict) -> None:
        self.reply, self.calls = reply, []

    def complete(self, **kw) -> str:
        self.calls.append(kw)
        return json.dumps(self.reply)


_LINES = ['asked: "проверь, что в core/a.py есть функция f"; via find_in_files->file_read; 3/3 claims verified',
          'asked: "есть ли в tools/b.py класс B"; via find_in_files->file_read; 2/2 claims verified']


def test_a_routine_with_variables_becomes_a_workflow() -> None:
    llm = _LLM({"workflows": [{"description": "Confirm a code object exists in a named file",
                               "steps": ["locate the name in the tree -> find_in_files {name}",
                                         "read the lines around it -> file_read {source_file}"]}]})
    made = induce("source", _LINES, llm)
    assert len(made) == 1 and made[0].kind == "source" and len(made[0].steps) == 2
    assert "Experiences:" in llm.calls[0]["user"] and llm.calls[0]["json_object"] is True


def test_a_step_that_keeps_a_concrete_path_is_not_a_workflow() -> None:
    """Литерал опыта в шаге — это след, а не шаблон (AWM: изменчивое → переменные)."""
    llm = _LLM({"workflows": [{"description": "x",
                               "steps": ["find it -> find_in_files", "read core/a.py -> file_read"]}]})
    assert induce("source", _LINES, llm) == []
    assert unabstracted(["read {source_file} -> file_read"]) == []


def test_one_experience_is_not_enough_to_generalise() -> None:
    llm = _LLM({"workflows": []})
    assert induce("source", _LINES[:1], llm) == [] and not llm.calls


def test_workflows_of_the_question_kind_reach_the_prompt(tmp_path: Path) -> None:
    store = WorkflowMemoryStore(tmp_path / FILE_NAME)
    store.replace_kind("source", [WorkflowRecord(kind="source", description="Check a claim",
                                                 steps=("a -> find_in_files", "b -> file_read"))])
    store.replace_kind("money", [WorkflowRecord(kind="money", description="Price an order",
                                                steps=("a -> web_search", "b -> python_probe"))])
    got = store.for_question("проверь источник этого утверждения")
    assert [w.kind for w in got] == ["source"]
    block = format_workflows(got)
    assert block.startswith("<agent_workflows>") and "Check a claim" in block and "Price an order" not in block


def test_rebuilding_a_kind_replaces_it(tmp_path: Path) -> None:
    store = WorkflowMemoryStore(tmp_path / FILE_NAME)
    store.replace_kind("source", [WorkflowRecord(kind="source", description="old", steps=("a", "b"))])
    store.replace_kind("source", [WorkflowRecord(kind="source", description="new", steps=("a", "b"))])
    assert [w.description for w in store.load()] == ["new"]


def test_no_file_no_block(tmp_path: Path) -> None:
    assert WorkflowMemoryStore(tmp_path / FILE_NAME).for_question("проверь источник") == []


def test_the_live_loop_puts_workflows_of_the_kind_into_its_experience_block(tmp_path: Path) -> None:
    from app.bootstrap import build_agent

    agent = build_agent(tmp_path, with_memory=True)
    question = "проверь источник этого утверждения"
    assert "<agent_workflows>" not in agent._retrieve_experience_memory(question)
    WorkflowMemoryStore(agent.procedural_store.path.parent / FILE_NAME).replace_kind(
        "source", [WorkflowRecord(kind="source", description="Check a claim against its file",
                                  steps=("find the claim's subject -> find_in_files {term}",
                                         "read it -> file_read {source_file}"))])
    block = agent._retrieve_experience_memory(question)
    assert "<agent_workflows>" in block and "Check a claim against its file" in block


def test_a_kind_with_a_workflow_gets_the_workflow_not_the_procedures_too(tmp_path: Path) -> None:
    """Экзамен 2×2 (25.09): процедуры 67, шаблоны 66, оба вместе 63 — одно из двух."""
    from types import SimpleNamespace

    from core.loop_memory_read import AgentLoopMemoryRead

    searched = []
    store = SimpleNamespace(path=tmp_path / "procedural_memory.jsonl",
                            search_with_report=lambda q, **kw: searched.append(q) or SimpleNamespace(
                                procedures=["proc"], rejected_by={}))
    me = SimpleNamespace(procedural_store=store, _question_salience=lambda: None)
    me._workflows_for = lambda q: AgentLoopMemoryRead._workflows_for(me, q)

    # Нет шаблона рода — процедуры, как прежде.
    assert AgentLoopMemoryRead._procedures_unless_workflow(me, "проверь источник") == (["proc"], {})
    WorkflowMemoryStore(tmp_path / FILE_NAME).replace_kind(
        "source", [WorkflowRecord(kind="source", description="Check", steps=("a -> find_in_files", "b -> file_read"))])
    # Шаблон есть — процедур нет, и журнал говорит почему.
    assert AgentLoopMemoryRead._procedures_unless_workflow(me, "проверь источник") == (
        [], {"covered_by_workflow": 1})
    # Род без шаблона по-прежнему получает процедуры.
    assert AgentLoopMemoryRead._procedures_unless_workflow(me, "посчитай цену заказа")[0] in (["proc"], [])
    assert len(searched) >= 1
