"""A step whose postprocessing broke must not be retried into the same break.

WHO NEEDS THIS FILE. `core/loop_step_execution.py:_execute_step` wraps its whole
postprocessing stretch — classify, injection scan, redact, cache store — in a
last-resort handler at :780. Unlike the two handlers inside the attempt loop
that swallow their failure whole, this one behaves well: it logs the error, it
invents no artifact, and it writes a `ReplanTrigger`.

The trigger's code is `unknown`, and `unknown` carries `max_occurrences=1`. That
single character of the budget table is what turns "postprocessing is broken"
into "stop", instead of "try the same broken pipeline again".

MEASURED 2026-08-09, the last producer of `_step_trigger_tls` to be walked. With
the code changed to `tool_error` — budget 2, one literal, nothing else touched —
the tool is invoked TWICE, the same postprocessing error is logged twice, and
7300 tests pass. The guard was reporting honestly the whole time and nothing was
listening to what it said.

Both halves are asserted, because only asserting the stop would pass equally
against a system that never ran the tool at all.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core import loop_step_execution
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry

_ANSWER = (
    "Conclusion: ok. [general-knowledge]\nFacts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)


class _Fine(Tool):
    """Succeeds and returns something harmless: the break is downstream of it."""

    def __init__(self) -> None:
        self.name = "fine_tool"
        self.description = "returns harmless output"
        self.risk = "read_only"
        self.calls = 0

    def run(self, **_kwargs: object) -> str:
        self.calls += 1
        return "harmless output"

    def validate_output(self, _output: object) -> tuple[bool, list[str]]:
        return True, []


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("boom")


def _run(*, break_postprocessing: bool) -> tuple[_Fine, list[dict]]:
    workspace = Path(tempfile.mkdtemp(prefix="postproc-guard-"))
    tool = _Fine()
    registry = ToolRegistry()
    registry.register(tool)
    trace_id = new_trace_id()
    agent = AgentLoop(
        planner=FakePlanner(sources=[{
            "tool": "fine_tool", "arguments": {}, "label": "x:1",
            "expected_outcome": "something",
        }]),
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 5),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs",
                           verbose=False),
        memory=None,
        max_replan_attempts=3,   # the shipped default; at 1 the global cap wins
    )
    original = loop_step_execution.classify
    if break_postprocessing:
        loop_step_execution.classify = _boom
    try:
        # Concrete question: a vague one is stopped by the clarification gate.
        agent.run("что в doc.txt")
    finally:
        loop_step_execution.classify = original
    events = [
        json.loads(line)
        for line in (workspace / "logs" / f"{trace_id}.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return tool, events


def _stop_reason(events: list[dict]) -> str:
    for event in events:
        if event.get("event") == "replan_exhausted":
            return str(event["payload"].get("decision_reason", ""))
    return ""


def test_a_healthy_run_reaches_evidence_in_one_invocation() -> None:
    """GUARD: without this, 'called once' would also describe a broken fixture."""
    tool, events = _run(break_postprocessing=False)
    assert tool.calls == 1
    collected = [e for e in events if e.get("event") == "evidence_collected"]
    assert collected and collected[-1]["payload"]["count"] == 1


def test_broken_postprocessing_stops_the_run_instead_of_repeating_it() -> None:
    tool, events = _run(break_postprocessing=True)

    logged = [
        e for e in events
        if e.get("event") == "error"
        and "Postprocessing error" in str(e["payload"].get("message", ""))
    ]
    assert logged, "precondition: the last-resort handler must have fired"
    assert tool.calls == 1, (
        "the tool was run again with the same broken postprocessing pipeline; "
        "`unknown` carries max_occurrences=1 precisely so that cannot happen"
    )
    assert "unknown budget exhausted" in _stop_reason(events), (
        "the stop must come from the unknown budget by name — a stop produced "
        "by the global cap would leave this constant untested"
    )


def test_nothing_is_banked_as_evidence_from_a_failed_postprocessing() -> None:
    _tool, events = _run(break_postprocessing=True)
    collected = [e for e in events if e.get("event") == "evidence_collected"]
    assert collected and collected[-1]["payload"]["count"] == 0
