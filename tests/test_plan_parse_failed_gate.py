"""Planner JSON parse failure must NOT silently fall through to a
zero-evidence "general knowledge" answer.

Live trace observation: when the planner LLM emitted non-JSON
(`json_decode_error` -> `plan_parse_failed`), the loop's empty-sources
fall-through treated the run as an intentional 0-step plan. The
synthesizer then produced a long answer with `verified_chunks=0` and
`evidence_score=0.0`. This module pins the new behaviour: parse failure
becomes a real `ReplanTrigger("plan_parse_failed")`, the policy can
retry with a hard JSON-only reminder, and on exhaustion the loop trips
`replan_exhausted` instead of synthesising over nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import PlannerOutput
from core.policy import PolicyGate
from tools.base import Tool, ToolRegistry
from tests.conftest import FakeLLM


class _ScriptedPlannerWithWarnings:
    """Each call returns (sources, warnings) from the next script entry."""

    def __init__(self, scripts: list[tuple[list[dict[str, Any]], list[str]]]):
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    def plan(
        self,
        question: str,
        file_hint: str | None = None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
        llm: Any = None,
    ) -> PlannerOutput:
        self.calls.append(
            {
                "question": question,
                "failure_context": failure_context,
                "call_index": len(self.calls),
            }
        )
        idx = len(self.calls) - 1
        if idx < len(self.scripts):
            sources, warnings = self.scripts[idx]
        else:
            sources, warnings = ([], [])
        for src in sources:
            src.setdefault("expected_outcome", "executes the planned step")
        return PlannerOutput(
            reasoning="scripted",
            sources=sources,
            raw_response="raw bytes that did not parse" if "plan_parse_failed" in warnings else "",
            warnings=list(warnings),
        )


class _StubSearchTool(Tool):
    name = "web_search"
    description = "deterministic stub"
    risk = "read_only"

    def run(self, query: str, max_results: int = 3) -> list[dict[str, str]]:
        del max_results
        return [{
            "title": "Stub",
            "url": "https://example.com/x",
            "snippet": query,
            "source": "stub",
        }]

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        return (isinstance(output, list), [])


def _events(p: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_agent(
    workspace: Path,
    planner: _ScriptedPlannerWithWarnings,
    llm_responses: list[str],
    *,
    max_replan_attempts: int = 3,
) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(_StubSearchTool())
    llm = FakeLLM(responses=llm_responses)
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=max_replan_attempts,
        verifier_enabled=False,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


class TestPlanParseFailedGate:
    def test_parse_failed_then_clean_plan_recovers(self, workspace: Path):
        # First call: parse failure (empty sources + warning).
        # Second call: the real plan. The loop must replan, run web_search,
        # and finish with a normal synthesis — not skip into general
        # knowledge after the empty first plan.
        planner = _ScriptedPlannerWithWarnings([
            ([], ["json_decode_error", "plan_parse_failed"]),
            (
                [{"tool": "web_search", "arguments": {"query": "x"},
                  "label": "search:x"}],
                [],
            ),
        ])
        synth_answer = (
            "Conclusion: stub answer [web:x].\n"
            "Facts: stub [web:x].\n"
            "Sources: stub\n"
            "Confidence: low\n"
            "Unverified: nothing\n"
            "Safety: ok"
        )
        agent, log_path = _build_agent(
            workspace, planner, llm_responses=[synth_answer]
        )
        agent.run("test question")

        events = _events(log_path)
        # 1. plan_parse_failed event was emitted.
        ppf = [e for e in events if e["event"] == "plan_parse_failed"]
        assert len(ppf) == 1
        assert ppf[0]["payload"]["attempt"] == 1
        # 2. A replan was kicked off whose triggers contain plan_parse_failed.
        replans = [e for e in events if e["event"] == "replan"]
        assert len(replans) >= 1
        assert "plan_parse_failed" in replans[0]["payload"]["triggers"]
        # 3. Planner was called twice — the parse failure forced a retry.
        assert len(planner.calls) == 2
        # 4. The 2nd planner call carries the parse-failure advice.
        ctx2 = planner.calls[1]["failure_context"].lower()
        assert "json" in ctx2
        # 5. The recovery attempt actually produced an artifact.
        plans = [e for e in events if e["event"] == "plan"]
        assert any(p.get("extra", {}).get("steps") == 1 for p in plans)

    def test_parse_failed_twice_aborts_with_replan_exhausted(
        self, workspace: Path
    ):
        # Budget for plan_parse_failed is max_occurrences=2 -> after the
        # second occurrence the policy returns abort_no_retry. The loop
        # must then emit replan_exhausted instead of letting the
        # synthesizer write a long confident answer over zero evidence.
        planner = _ScriptedPlannerWithWarnings([
            ([], ["json_decode_error", "plan_parse_failed"]),
            ([], ["json_decode_error", "plan_parse_failed"]),
        ])
        # Synthesizer still runs once (over the empty / honest-failure
        # path) to produce the user-facing reply, but the agent must
        # have logged exhaustion first.
        fallback_answer = (
            "Conclusion: I could not plan this turn.\n"
            "Facts: planner JSON parse failed.\n"
            "Sources: none\n"
            "Confidence: low\n"
            "Unverified: everything\n"
            "Safety: ok"
        )
        agent, log_path = _build_agent(
            workspace, planner, llm_responses=[fallback_answer]
        )
        agent.run("test question")

        events = _events(log_path)
        ppf = [e for e in events if e["event"] == "plan_parse_failed"]
        assert len(ppf) == 2
        exhausted = [e for e in events if e["event"] == "replan_exhausted"]
        assert len(exhausted) == 1
        assert "plan_parse_failed" in exhausted[0]["payload"]["triggers"]

    def test_clean_empty_plan_still_treated_as_success(self, workspace: Path):
        # Regression guard: an INTENTIONAL 0-step plan (no parse failure,
        # no warnings) is the general-knowledge contract and must NOT be
        # demoted to a failure by the new gate.
        planner = _ScriptedPlannerWithWarnings([([], [])])
        synth_answer = (
            "Conclusion: 4 [general-knowledge].\n"
            "Facts: 2+2=4 [general-knowledge].\n"
            "Sources: general\n"
            "Confidence: medium\n"
            "Unverified: none\n"
            "Safety: ok"
        )
        agent, log_path = _build_agent(
            workspace, planner, llm_responses=[synth_answer]
        )
        agent.run("what is 2+2?")

        events = _events(log_path)
        # Exactly one planner call and NO parse-failed / replan events.
        assert len(planner.calls) == 1
        assert not any(e["event"] == "plan_parse_failed" for e in events)
        assert not any(e["event"] == "replan_exhausted" for e in events)
