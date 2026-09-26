"""A batch of one step passes the same gates as a longer batch: its write text is composed first."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.loop_step_execution import AgentLoopStepExecution
from core.models import PlanStep


class _LLM:
    def complete(self, **_kw: Any) -> str:
        return "итог хода"


class _Loop(AgentLoopStepExecution):
    def __init__(self) -> None:
        self.llm = _LLM()
        self.model_router = None
        self.log = SimpleNamespace(log=lambda *_a, **_k: None)
        self.ran: list[dict[str, Any]] = []

    def _step_only_reads(self, step: PlanStep) -> bool:
        return step.action_spec.get("tool_name") != "file_write"

    def _run_step_parallel(self, step: PlanStep):  # the tool call itself
        self.ran.append(dict(step.action_spec.get("arguments") or {}))
        return step, {"output": "ok"}, None


def _write(instruction: str) -> PlanStep:
    return PlanStep(plan_id="p", order=1, expected_outcome="file written", action_spec={
        "tool_name": "file_write", "arguments": {"path": "out.txt", "write_instruction": instruction}})


def test_a_lone_write_reaches_the_tool_with_composed_text() -> None:
    loop = _Loop()

    loop._execute_steps_parallel([_write("перескажи итог")])

    assert loop.ran == [{"path": "out.txt", "content": "итог хода"}], \
        "одиночная запись ушла в инструмент без собранного текста"


def test_a_lone_write_and_a_pair_are_treated_alike() -> None:
    lone, pair = _Loop(), _Loop()
    read = PlanStep(plan_id="p", order=0, expected_outcome="read", action_spec={
        "tool_name": "file_read", "arguments": {"path": "a.txt"}})

    lone._execute_steps_parallel([_write("x")])
    pair._execute_steps_parallel([read, _write("x")])

    assert lone.ran[-1] == pair.ran[-1]
