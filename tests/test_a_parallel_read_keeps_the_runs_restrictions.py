"""Параллельный шаг видит ограничения своего прогона.

Замер 2026-09-19 (экзамен агента, задача «закрытая сеть», и эксперимент с
учёбой). Ограничения прогона — закрытые инструменты, требование dry-run,
идентификатор прогона — живут в ContextVar (`core/run_context.py`). План из
одних чтений исполняется в ThreadPoolExecutor, а `executor.submit` контекст в
поток не переносит. Итог: при закрытой сети web_search и web_fetch выполнились,
при закрытых файлах выполнились file_read и list_dir. Планы с записью это не
задевало — они идут по порядку в основном потоке.
"""
from __future__ import annotations

from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import PlanStep
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry
from tools.file_read import FileReadTool
from tools.list_dir import ListDirTool


def _loop(workspace: Path, *extra: Tool) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(ListDirTool(workspace_root=workspace))
    for tool in extra:
        registry.register(tool)
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[]),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[]),
        approval_provider=AutoApprover(default="approve"),
    )


def _step(tool: str, arguments: dict, order: int) -> PlanStep:
    return PlanStep(
        plan_id="plan_x", order=order,
        action_spec={"type": "tool_call", "tool_name": tool, "arguments": arguments},
        expected_outcome="whatever",
    )


def _two_reads() -> list[PlanStep]:
    return [_step("file_read", {"path": "a.txt"}, 1), _step("list_dir", {"path": "."}, 2)]


def test_the_same_plan_runs_when_nothing_is_blocked(workspace: Path):
    """Контроль: ветка действительно параллельная и без запрета работает."""
    (workspace / "a.txt").write_text("secret", encoding="utf-8")
    loop = _loop(workspace)

    results = loop._execute_steps_parallel(_two_reads())

    assert all(outcome is not None for _, outcome, _ in results)
