"""A shell command that timed out must be told to reduce scope, not to reformulate.

WHO NEEDS THIS FILE. The four literal producers of `_step_trigger_tls` were
measured one at a time on 2026-08-09, each with the same isolated mutation —
change ONLY the `ReplanTrigger` code to another code of the same budget, leaving
the `ErrorObject` above it alone, so control flow is identical and only the
advice text moves. Three of the four were already watched:

    injection_blocked (:654)  unprotected  -> guarded by this program
    verify_failed     (:529)  protected    -> 3 pre-existing tests
    web_empty         (:556)  protected    -> 2 pre-existing tests
    timeout           (:577)  UNPROTECTED  -> 7297 passed with it swapped

This file closes the last one, with the assertion shape the other two already
had: `tests/test_replan.py::test_failure_context_contains_code_tool_reason`
checks that the planner's retry context names the code. That pattern existed in
this repository and had not been carried to every producer.

WHY IT MATTERS BEYOND BOOKKEEPING. Each code carries its own advice text in
`core/replan.py`, and the advice is what the planner reads before it decides
what to try next. `timeout` says REDUCE SCOPE — ask for less data. `web_empty`
says REFORMULATE the query. A shell command that ran out of time and was told to
rephrase its query has been given advice for a different failure.

SCOPE. Asserted at DELIVERY, the last hop a mock can observe. What a live model
does with the advice is not claimed here.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.replan import DEFAULT_BUDGETS
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry

_ANSWER = (
    "Conclusion: ok. [general-knowledge]\nFacts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)


class _TimingOutShell(Tool):
    """Stands in for shell_exec and returns the timeout shape the loop reads.

    The loop's condition is `tool_name == "shell_exec"` AND a dict output whose
    `timed_out` is true, so the name is part of the fixture, not decoration.
    """

    def __init__(self) -> None:
        self.name = "shell_exec"
        self.description = "runs a command"
        self.risk = "read_only"
        self.calls = 0

    def run(self, **_kwargs: object) -> dict:
        self.calls += 1
        return {"stdout": "partial", "stderr": "", "exit_code": None, "timed_out": True}

    def validate_output(self, _output: object) -> tuple[bool, list[str]]:
        return True, []


def _run() -> tuple[_TimingOutShell, FakePlanner]:
    workspace = Path(tempfile.mkdtemp(prefix="shell-timeout-"))
    tool = _TimingOutShell()
    registry = ToolRegistry()
    registry.register(tool)
    planner = FakePlanner(sources=[{
        "tool": "shell_exec", "arguments": {"command": "sleep 999"},
        "label": "shell:sleep", "expected_outcome": "output",
    }])
    trace_id = new_trace_id()
    agent = AgentLoop(
        planner=planner,
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 5),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs",
                           verbose=False),
        memory=None,
        # The SHIPPED default. At 1 the global cap fires first and the per-type
        # budget is never consulted, which is how this producer stayed invisible.
        max_replan_attempts=3,
    )
    # A concrete question: a vague one is stopped by the clarification gate
    # before the planner is ever called, and the test then measures nothing.
    agent.run("что в doc.txt")
    return tool, planner


def test_the_timeout_buys_exactly_one_retry() -> None:
    """Precondition: the producer must actually fire and its budget must apply."""
    tool, planner = _run()
    assert tool.calls == 2, (
        "timeout carries max_occurrences=2, so the first hit must not end the run"
    )
    assert len(planner.calls) == 2


def test_the_retry_prompt_names_the_timeout_rather_than_some_other_failure() -> None:
    _tool, planner = _run()
    first, retry = planner.calls[0]["failure_context"], planner.calls[1]["failure_context"]
    assert first == "", "attempt 1 must carry no failure context"
    assert "timeout" in retry, (
        "the planner is told a step failed but not that it ran out of time; the "
        "trigger's code is what carries that"
    )
    assert "web_empty" not in retry


def test_the_advice_the_planner_reads_is_the_timeout_advice() -> None:
    """The consequence the code selects, not just the code's own spelling."""
    _tool, planner = _run()
    retry = planner.calls[1]["failure_context"]
    assert DEFAULT_BUDGETS["timeout"].advice[:40] in retry
    assert DEFAULT_BUDGETS["web_empty"].advice[:40] not in retry, (
        "advice for a different failure type would send the planner the wrong way"
    )
