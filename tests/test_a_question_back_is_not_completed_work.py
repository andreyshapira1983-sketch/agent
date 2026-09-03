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

import json
from pathlib import Path

import pytest

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


def _events(agent: AgentLoop) -> list[dict]:
    return [
        json.loads(line)
        for line in agent.log.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_the_loop_leaves_a_machine_readable_trace_of_the_question(workspace: Path):
    """Зелёный дискриминатор: след есть — событие `clarification_request`.
    Потеря, значит, ниже по течению."""
    agent = _asking_agent(workspace)
    runtime = AutonomousRuntime(agent, workspace=workspace)
    task = AutonomousTask(kind="goal", description="analyze core/replan.py")

    runtime._task_goal(task, AutonomousRuntimeConfig(dry_run=True))

    kinds = [e.get("event") for e in _events(agent)]
    assert "clarification_request" in kinds


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-09-03 and banked rather than fixed (RED witness "
        "by the operator's word): AutonomousRuntime._task_goal wraps ANY non-empty "
        "answer as status 'done', so the clarification gate's question back "
        "('Я не могу безопасно продолжить. Уточни: …') becomes completed / "
        "work_done=True / useful=1 (charter_day4_reasoner cycle 1, 287 cost units). "
        "The loop DOES leave a machine-readable trace (event clarification_request "
        "— the green test above); the runtime does not read it. Minimal repair "
        "proposed, not applied: have the gate mark the loop (e.g. "
        "agent.last_answer_was_clarification) and let _task_goal return status "
        "'clarify' with details['clarification'], exactly as the replan_exhausted "
        "branch already does. "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_a_question_back_is_reported_as_clarify_not_done(workspace: Path):
    """Красный свидетель: тот же прогон — статус задачи обязан быть «clarify»,
    как у ветки replan_exhausted, а не «done»."""
    agent = _asking_agent(workspace)
    runtime = AutonomousRuntime(agent, workspace=workspace)
    task = AutonomousTask(kind="goal", description="analyze core/replan.py")

    report = runtime._task_goal(task, AutonomousRuntimeConfig(dry_run=True))

    assert report.details.get("answer", "").startswith("Я не могу безопасно продолжить"), (
        "предпосылка: петля вернула именно встречный вопрос"
    )
    assert report.status == "clarify", (
        f"встречный вопрос записан как {report.status!r} — и дальше станет "
        "completed/work_done=True/useful=1 (замер 2026-09-03, cycle 1, 287 единиц)"
    )
    assert report.details.get("clarification"), (
        "у ветки replan_exhausted вопрос едет в details['clarification'] — "
        "у ворот уточнения обязан ехать так же"
    )
