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
from core.run_context import current_run, run_restrictions, run_scope
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


def test_a_blocked_read_does_not_run_in_a_worker_thread(workspace: Path):
    (workspace / "a.txt").write_text("secret", encoding="utf-8")
    loop = _loop(workspace)

    with run_restrictions(blocked_tools={"file_read", "list_dir"}):
        results = loop._execute_steps_parallel(_two_reads())

    assert len(results) == 2, "план из двух чтений — параллельная ветка"
    assert all(outcome is None for _, outcome, _ in results), (
        "закрытый инструмент выполнился в рабочем потоке: ограничение прогона потерялось"
    )
    assert {trigger.code for _, _, trigger in results} == {"policy_blocked"}


def test_the_same_plan_runs_when_nothing_is_blocked(workspace: Path):
    """Контроль: ветка действительно параллельная и без запрета работает."""
    (workspace / "a.txt").write_text("secret", encoding="utf-8")
    loop = _loop(workspace)

    results = loop._execute_steps_parallel(_two_reads())

    assert all(outcome is not None for _, outcome, _ in results)


class _WhoAmI(Tool):
    """Читающий инструмент, который сообщает, какой прогон он видит."""

    name = "who_am_i"
    description = "report the active run id"
    risk = "read_only"

    def run(self, **_kwargs) -> dict:
        ctx = current_run()
        return {"run_id": ctx.run_id if ctx else None}


def test_a_worker_thread_knows_which_run_it_serves(workspace: Path):
    loop = _loop(workspace, _WhoAmI())
    steps = [_step("who_am_i", {}, 1), _step("who_am_i", {}, 2)]

    with run_scope("run_parallel_probe"):
        results = loop._execute_steps_parallel(steps)

    seen = {outcome["output"]["run_id"] for _, outcome, _ in results if outcome}
    assert seen == {"run_parallel_probe"}, f"поток видел прогоны {seen}"
