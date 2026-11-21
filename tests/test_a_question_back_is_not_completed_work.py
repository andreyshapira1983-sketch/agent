"""Встречный вопрос — не сделанная работа.

Замер 2026-09-03, прогон charter_day4_reasoner, цикл 1: цель — расследовать
эпизод, записанный как success 1.0 при отказе производителя. Ответ петли,
дословно: «Я не могу безопасно продолжить. Уточни: что именно нужно построить?
где? что считать готовым результатом?» Это ворота уточнения — петля честно
остановилась перед двусмысленной задачей. А бухгалтерия записала:
result=completed, work_done=True, 287 единиц стоимости, useful=1.
Расследуя чужой фальшивый success, цикл произвёл свой.

Механика: `_clarification_gate` возвращает текст вопроса КАК ОТВЕТ петли
(core/loop.py, `return _decided`), и `AutonomousRuntime._task_goal` заворачивает
любой непустой ответ в статус «done». Статус «clarify» в рантайме СУЩЕСТВУЕТ —
но только для одной ветки (replan_exhausted); вопрос с ворот уточнения в него
не попадает. Дальше `semantic_result()` честно считает «done» работой.

Дискриминатор: петля СЛЕД ОСТАВЛЯЕТ — событие `clarification_request` в
трассе. Значит, потеря не в петле, а на шве рантайма: след есть, его не читают.
"""
from __future__ import annotations

from pathlib import Path

from core.approval import AutoApprover
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.clarification_policy import ClarificationResult
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry

_QUESTION = (
    "Я не могу безопасно продолжить. Уточни:\n"
    "- Что именно нужно построить?\n"
    "- Где это должно работать (target file / source / окружение)?\n"
    "- Что считать готовым результатом?"
)


def _agent(workspace: Path) -> AgentLoop:
    registry = ToolRegistry()
    llm = FakeLLM(responses=[])
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )


def _asking_agent(workspace: Path) -> AgentLoop:
    agent = _agent(workspace)
    agent.clarification_enabled = True
    # Настоящие ворота, настоящий путь возврата; подменён только вердикт
    # эвристики — чтобы вопрос гарантированно возник.
    agent._check_clarification = lambda _q: ClarificationResult(  # type: ignore[method-assign]
        decision="ask", question=_QUESTION,
    )
    return agent


def test_a_question_back_that_arrived_earlier_does_not_taint_the_next_goal(workspace: Path):
    """Флаг ворот — свойство ОДНОГО прогона петли: вопрос на прошлом вопросе
    не должен перекрашивать следующий настоящий ответ в «clarify»."""
    agent = _asking_agent(workspace)
    runtime = AutonomousRuntime(agent, workspace=workspace)
    runtime._task_goal(AutonomousTask(kind="goal", description="analyze core/replan.py"),
                       AutonomousRuntimeConfig(dry_run=True))
    agent._check_clarification = lambda _q: ClarificationResult(decision="proceed")  # type: ignore[method-assign]
    agent.run = lambda *, user_question: "analysis: done for real"  # type: ignore[method-assign]

    report = runtime._task_goal(AutonomousTask(kind="goal", description="analyze core/replan.py"),
                                AutonomousRuntimeConfig(dry_run=True))

    assert report.status == "done"
