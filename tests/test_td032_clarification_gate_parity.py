"""TD-032 slice 3 — clarification_gate on autonomous runtime / daemon paths."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.planner import PlannerOutput
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry


class _ScriptedPlannerWithWarnings:
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
        idx = len(self.calls)
        self.calls.append({"question": question, "call_index": idx})
        if idx < len(self.scripts):
            sources, warnings = self.scripts[idx]
        else:
            sources, warnings = ([], [])
        return PlannerOutput(
            reasoning="scripted",
            sources=sources,
            raw_response="",
            warnings=list(warnings),
        )


class _StubSearchTool(Tool):
    name = "web_search"
    description = "deterministic stub"
    risk = "read_only"

    def run(self, query: str, max_results: int = 3) -> list[dict[str, str]]:
        del max_results
        return [{"title": "Stub", "url": "https://example.com/x", "snippet": query}]

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        return (isinstance(output, list), [])


class GoalOnlyRuntime(AutonomousRuntime):
    def _build_queue(self, config: AutonomousRuntimeConfig) -> list[AutonomousTask]:
        return [AutonomousTask("goal", config.goal or "test question")]


def _events(log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _agent(
    workspace: Path,
    planner: _ScriptedPlannerWithWarnings,
    llm_responses: list[str],
) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(_StubSearchTool())
    llm = FakeLLM(responses=llm_responses)
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        memory=WorkingMemory(),
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=3,
        verifier_enabled=False,
    )
    return agent, log_path


def test_goal_task_replan_exhausted_returns_clarify_status(workspace: Path) -> None:
    planner = _ScriptedPlannerWithWarnings([
        ([], ["json_decode_error", "plan_parse_failed"]),
        ([], ["json_decode_error", "plan_parse_failed"]),
    ])
    fallback = (
        "Conclusion: I could not plan this turn.\n"
        "Facts: planner JSON parse failed.\n"
        "Sources: none"
    )
    agent, _ = _agent(workspace, planner, [fallback])
    report = GoalOnlyRuntime(
        agent,
        workspace=workspace,
        approval_inbox=ApprovalInbox(),
        receipt_path="runtime",
    ).run(
        AutonomousRuntimeConfig(
            goal="ambiguous unattended goal",
            limit=1,
            include_tests=False,
            enable_reflection=False,
        )
    )

    goal = next(t for t in report.tasks if t.task.kind == "goal")
    assert goal.status == "clarify"
    assert goal.details.get("stop_reason") == "replan_exhausted"
    assert goal.details.get("clarification", {}).get("mode") == "clarify"
    assert agent.last_replan_exhausted is True


def test_runtime_layer_logs_clarification_gate_on_stuck_goal(workspace: Path) -> None:
    planner = _ScriptedPlannerWithWarnings([
        ([], ["json_decode_error", "plan_parse_failed"]),
        ([], ["json_decode_error", "plan_parse_failed"]),
    ])
    fallback = "Conclusion: stuck.\nFacts: none.\nSources: none"
    agent, log_path = _agent(workspace, planner, [fallback])
    GoalOnlyRuntime(
        agent,
        workspace=workspace,
        approval_inbox=ApprovalInbox(),
        receipt_path="daemon",
    ).run(
        AutonomousRuntimeConfig(
            goal="daemon goal stuck",
            limit=1,
            include_tests=False,
            enable_reflection=False,
        )
    )

    gate_events = [
        e
        for e in _events(log_path)
        if e["event"] == "clarification_gate"
        and e.get("payload", {}).get("path") == "daemon"
    ]
    assert len(gate_events) == 1
    payload = gate_events[0]["payload"]
    assert payload["trigger"] == "replan_exhausted"
    assert payload["mode"] == "clarify"
    assert payload["questions"]


def test_goal_task_without_replan_exhausted_stays_done(workspace: Path) -> None:
    planner = _ScriptedPlannerWithWarnings([([], [])])
    agent, log_path = _agent(workspace, planner, ["ignored"])
    agent.run = lambda user_question: "grounded answer"  # type: ignore[method-assign]
    agent.last_replan_exhausted = False
    report = GoalOnlyRuntime(
        agent,
        workspace=workspace,
        approval_inbox=ApprovalInbox(),
    ).run(
        AutonomousRuntimeConfig(
            goal="simple lookup",
            limit=1,
            include_tests=False,
            enable_reflection=False,
        )
    )

    goal = next(t for t in report.tasks if t.task.kind == "goal")
    assert goal.status == "done"
    assert agent.last_replan_exhausted is False
    runtime_gate = [
        e
        for e in _events(log_path)
        if e["event"] == "clarification_gate"
        and e.get("payload", {}).get("path") in {"runtime", "daemon"}
    ]
    assert runtime_gate == []
