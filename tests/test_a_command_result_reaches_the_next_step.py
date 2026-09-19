"""Результат команды доезжает до следующего шага её выводом, а не словарём.

Замер 2026-09-19, внешний экзамен (задачи «сумма по именам» и «пересчитать
сумму»): лаборатория верно посчитала, `file_write(content={{step:1.output}})`
получил словарь {code, stdout, exit_code, …} и упал «content must be a string,
got dict». Попытки сгорали, в файле оставалась старая сумма.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import PlanStep
from core.policy import PolicyGate
from core.step_references import UnresolvedStepReference, resolve_step_references
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool
from tools.python_probe import PythonProbeTool


def _cmd(stdout: str = "42\n", **kw) -> dict:
    return {"code": "print(42)", "stdout": stdout, "stderr": "", "exit_code": 0,
            "timed_out": False, "stdout_truncated": False, **kw}


def test_a_whole_argument_reference_carries_the_commands_stdout():
    assert resolve_step_references({"content": "{{step:1.output}}"}, {"1": _cmd()}) == {"content": "42\n"}


def test_a_reference_inside_text_carries_the_stdout_too():
    assert resolve_step_references({"content": "sum={{step:1.output}}"}, {"1": _cmd("7")}) == {
        "content": "sum=7"}


@pytest.mark.parametrize(("output", "reason"), [
    (_cmd("", exit_code=1, stderr="IndexError: list index out of range"), "exited with 1"),
    (_cmd("", exit_code=None, timed_out=True), "timed out"),
    (_cmd("12", stdout_truncated=True), "truncated"),
    (_cmd("exists: False", missing_inputs=["numbers.txt"]), "not given"),
])
def test_a_failed_command_carries_no_result(output: dict, reason: str):
    with pytest.raises(UnresolvedStepReference, match=reason):
        resolve_step_references({"content": "{{step:1.output}}"}, {"1": output})


def test_a_non_command_result_keeps_its_type():
    """Прежний договор цел: список остаётся списком, словарь без stdout — словарём."""
    assert resolve_step_references({"x": "{{step:1.output}}"}, {"1": ["a", "b"]}) == {"x": ["a", "b"]}
    assert resolve_step_references({"x": "{{step:1.output}}"}, {"1": {"a": 1}}) == {"x": {"a": 1}}


def test_a_computed_sum_lands_in_the_file(workspace: Path):
    (workspace / "numbers.txt").write_text("214\n-310\n967\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(PythonProbeTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    loop = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=[]),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[]), approval_provider=AutoApprover(default="approve"),
    )
    steps = [
        PlanStep(plan_id="p", order=1, expected_outcome="sum", action_spec={
            "type": "tool_call", "tool_name": "python_probe",
            "arguments": {"code": "print(sum(int(x) for x in open('numbers.txt').read().split()))",
                          "inputs": ["numbers.txt"]}}),
        PlanStep(plan_id="p", order=2, expected_outcome="write", action_spec={
            "type": "tool_call", "tool_name": "file_write",
            "arguments": {"path": "sum.txt", "content": "{{step:1.output}}"}}),
    ]

    results = loop._execute_steps_parallel(steps)

    assert all(outcome is not None for _, outcome, _ in results), results
    assert (workspace / "sum.txt").read_text(encoding="utf-8").strip() == "871"
