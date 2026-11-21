"""Stuck on its own draft, the agent reports — it does not ask «what to build».

Web exam 2026-09-19: in 4 tasks of 9 the request was fully specified («latest
version of rich on PyPI», «RFC number for HTTP/2»). The loop exhausted its
replans because the draft's citations did not resolve — an internal fault —
and the clarification gate prepended «Я не могу безопасно продолжить. Уточни:
что именно нужно построить? где это должно работать? что считать готовым?».
The human has no answer to those questions; they were a hand-off, not a
clarification. The gate still asks when the cause is unknown or lies outside
the draft (a failing tool, a parse failure) — pinned by
test_plan_parse_failed_gate.py and test_response_composition.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry


class _Notes(Tool):
    def __init__(self) -> None:
        self.name = "file_read"
        self.description = "read"
        self.risk = "read_only"

    def run(self, **kwargs: Any) -> Any:
        return "release notes: nothing about versions"


def test_a_draft_citing_an_unfetched_page_is_not_turned_into_questions(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_Notes())
    draft = ("Conclusion: the latest version is 9.9.9 [web:https://pypi.org/project/demo/]\n"
             "Facts:\n- version 9.9.9 [web:https://pypi.org/project/demo/]\n"
             "Sources:\n1. web:https://pypi.org/project/demo/\nConfidence: medium\nUnverified: nothing\n")
    trace_id = new_trace_id()
    agent = AgentLoop(
        planner=FakePlanner(sources=[{"tool": "file_read", "arguments": {"path": "notes.txt"},
                                      "label": "file:notes.txt", "expected_outcome": "notes"}]),
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[draft] * 8),
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs", verbose=False),
        memory=None,
        max_replan_attempts=1,
    )
    answer = agent.run("какая последняя версия demo на PyPI?")
    events = [json.loads(line) for line in (tmp_path / "logs" / f"{trace_id}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    names = [e.get("event") for e in events]
    exhausted = [e["payload"] for e in events if e.get("event") == "replan_exhausted"]
    assert exhausted and exhausted[0].get("triggers") == ["unresolved_citation"], (
        f"precondition: the run must be stuck on its own citations; events: {sorted({n for n in names if n})}"
    )
    assert "clarification_gate" not in names
    assert "clarification_gate_skipped" in names
    assert "Что именно нужно построить" not in answer
