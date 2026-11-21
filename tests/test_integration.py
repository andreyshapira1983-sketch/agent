"""Full Control Loop integration test (§3 + §5 + §12.4 + §1).

End-to-end: CLI input -> Planner -> Plan -> Policy -> file_read -> ToolResult
-> Verify -> Synthesize. Asserted on both the returned answer AND the
structured JSONL trace log.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import PlanStep
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent(
    workspace: Path, llm: FakeLLM, *, cheap_path_enabled: bool = True
) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))

    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id,
        log_dir=workspace / "logs",
        verbose=False,
    )
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        cheap_path_enabled=cheap_path_enabled,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def test_cheap_path_skips_knowledge_pipeline_and_picks_light_synth(
    workspace: Path,
) -> None:
    """A cheap-path turn must not run the per-turn knowledge pipeline and must
    route the synthesizer through the LIGHT (cheap) model tier."""
    gk_answer = (
        "Conclusion: The flag disables effects. [general-knowledge]\n"
        "Facts:\n- A false flag turns a feature off. [general-knowledge]\n"
        "Sources:\n1. general-knowledge - general-knowledge\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    llm = FakeLLM(responses=[gk_answer])
    agent, log_path = _build_agent(workspace, llm)

    agent.run(user_question="effects=disabled", file_hint=None)

    names = {e["event"] for e in _events(log_path)}
    # Cheap path fired.
    assert "planner_cheap_path" in names
    # Knowledge pipeline + source-registry build were skipped for this turn.
    assert "knowledge_pipeline_skipped" in names
    assert "knowledge_pipeline" not in names
    assert "source_registry" not in names
    # The synthesizer model choice was re-routed via the cheap-path gate.
    assert "cheap_path_synth_model" in names


# ===========================================================
# Step ordering: concurrency is for reads only
# ===========================================================

class TestEffectStepsRunInPlanOrder:
    """A plan that changes the workspace is a sequence, not a set.

    Measured on a live run: a correct six-step plan (write, write, test,
    branch, add, commit) executed as run_tests -> checkout -> write -> add ->
    commit -> write. `git add` ran before the file existed and the commit
    failed after it. `preconditions` is what the parallel partition reads, and
    the planner never fills it, so every step looked independent.
    """

    def _loop(self, workspace: Path) -> AgentLoop:
        registry = ToolRegistry()
        registry.register(FileReadTool(workspace_root=workspace))
        registry.register(FileWriteTool(workspace_root=workspace))
        trace_id = new_trace_id()
        return AgentLoop(
            registry=registry,
            policy=PolicyGate(registry),
            llm=FakeLLM(responses=[]),
            logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
            planner=FakePlanner(sources=[]),
            approval_provider=AutoApprover(default="approve"),
        )

    @staticmethod
    def _step(tool_name: str, arguments: dict, order: int) -> PlanStep:
        return PlanStep(
            plan_id="plan_x",
            order=order,
            action_spec={"type": "tool_call", "tool_name": tool_name, "arguments": arguments},
            expected_outcome="whatever",
        )


    def test_an_unresolvable_step_counts_as_an_effect(self, workspace: Path):
        loop = self._loop(workspace)
        assert not loop._step_only_reads(self._step("no_such_tool", {}, 1))
        assert not loop._step_only_reads(
            PlanStep(plan_id="p", order=1, action_spec={}, expected_outcome="x")
        )
        assert loop._step_only_reads(self._step("file_read", {"path": "a.txt"}, 1))

    def test_a_tool_whose_risk_probe_raises_counts_as_an_effect(
        self, workspace: Path
    ):
        """The third unresolvable case, and the one nothing covered.

        Found by mutation 2026-08-20: flipping this branch's `return False` to
        `True` survived the whole suite. The siblings above pin the missing
        tool and the empty spec; a tool whose `risk_for` RAISES had no witness,
        so a step that may write anything could have joined the concurrent
        batch. The contract is three lines above the branch: anything
        unresolvable is treated as an effect.
        """
        loop = self._loop(workspace)

        class _RaisingRisk(FileReadTool):
            name = "risk_raiser"

            def risk_for(self, arguments):  # type: ignore[override]
                raise RuntimeError("risk probe is broken")

        loop.registry.register(_RaisingRisk(workspace_root=workspace))
        assert not loop._step_only_reads(self._step("risk_raiser", {"path": "a.txt"}, 1))
