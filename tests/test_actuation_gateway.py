"""Gateway slices G1–G2 — actuation gateway unit + path integration tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.actuation_gateway import (
    ActuationGateway,
    gateway_path_from_receipt,
    is_effectful_tool,
    simulate_output,
)
from core.approval import AutoApprover
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig, AutonomousTask
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import Action
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool
from tools.shell_exec import ShellExecTool


def _registry(workspace: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace, timeout_seconds=5.0))
    return reg


def test_is_effectful_file_write(workspace: Path) -> None:
    reg = _registry(workspace)
    assert is_effectful_tool("file_write", {"path": "a.txt", "content": "x"}, reg)


def test_is_effectful_shell_readonly_false(workspace: Path) -> None:
    reg = _registry(workspace)
    assert not is_effectful_tool(
        "shell_exec", {"argv": ["git", "status"]}, reg
    )


def test_is_effectful_shell_mutating_true(workspace: Path) -> None:
    reg = _registry(workspace)
    assert is_effectful_tool(
        "shell_exec", {"argv": ["mkdir", "x"]}, reg
    )


def test_is_effectful_non_g1_tool_false(workspace: Path) -> None:
    reg = _registry(workspace)
    assert not is_effectful_tool("file_read", {"path": "a.txt"}, reg)


def test_evaluate_passthrough_for_readonly_tool(workspace: Path) -> None:
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    gw = ActuationGateway(policy, path="repl")
    action = Action(step_id="s1", type="tool_call", tool_name="file_read", parameters={})
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "passthrough"


def test_evaluate_allow_reversible_file_write(workspace: Path) -> None:
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    gw = ActuationGateway(policy, path="repl", dry_run=False)
    action = Action(
        step_id="s1",
        type="tool_call",
        tool_name="file_write",
        parameters={"path": "new_file.txt", "content": "hello"},
    )
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "allow"
    assert decision.policy is not None
    assert decision.policy.decision == "allow"


def test_evaluate_deny_when_tool_blocked(workspace: Path) -> None:
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    policy.blocked_tools = frozenset({"file_write"})
    gw = ActuationGateway(policy, path="repl")
    action = Action(
        step_id="s1",
        type="tool_call",
        tool_name="file_write",
        parameters={"path": "x.txt", "content": "y"},
    )
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "deny"


def test_evaluate_escalate_on_overwrite(workspace: Path) -> None:
    target = workspace / "existing.txt"
    target.write_text("old", encoding="utf-8")
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    gw = ActuationGateway(policy, path="repl")
    action = Action(
        step_id="s1",
        type="tool_call",
        tool_name="file_write",
        parameters={"path": "existing.txt", "content": "new"},
    )
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "escalate"
    assert decision.policy is not None
    assert decision.policy.decision == "escalate"


def test_evaluate_simulate_when_dry_run(workspace: Path) -> None:
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    gw = ActuationGateway(policy, path="repl", dry_run=True)
    action = Action(
        step_id="s1",
        type="tool_call",
        tool_name="file_write",
        parameters={"path": "sim.txt", "content": "z"},
    )
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "simulate"
    assert "dry_run" in " ".join(decision.reasons)


def test_simulate_output_shape() -> None:
    out = simulate_output("file_write", {"path": "a.txt"})
    assert out["gateway"] == "simulate"
    assert out["status"] == "simulated"


def test_loop_gateway_dry_run_skips_file_write(workspace: Path) -> None:
    reg = _registry(workspace)
    llm = FakeLLM(responses=["[synthesised]"])
    planner = FakePlanner(
        [
            {
                "tool": "file_write",
                "arguments": {"path": "gateway_skip.txt", "content": "nope"},
                "label": "file_write:gateway_skip",
                "expected_outcome": "writes file",
            }
        ]
    )
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        clarification_enabled=False,
        gateway_dry_run=True,
    )
    agent.run("write gateway_skip.txt")

    assert not (workspace / "gateway_skip.txt").exists()
    parsed = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        e.get("event") == "gateway_decision"
        and e.get("payload", {}).get("outcome") == "simulate"
        for e in parsed
    )
    assert not any(e.get("event") == "tool_result" for e in parsed)


def test_loop_effectful_shell_readonly_bypasses_gateway_decision_path(
    workspace: Path,
) -> None:
    """Read-only shell_exec still uses policy path; no gateway_decision row."""
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    reg = _registry(workspace)
    llm = FakeLLM(responses=["[synthesised]"])
    planner = FakePlanner(
        [
            {
                "tool": "shell_exec",
                "arguments": {"argv": ["git", "status"]},
                "label": "shell_exec:git status",
                "expected_outcome": "shows status",
            }
        ]
    )
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        clarification_enabled=False,
    )
    agent.run("git status")

    kinds = [json.loads(line)["event"] for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert "gateway_decision" not in kinds
    assert "tool_call" in kinds


@pytest.mark.parametrize(
    ("receipt_path", "expected"),
    [("runtime", "runtime"), ("daemon", "daemon"), ("repl", "repl"), ("unknown", "runtime")],
)
def test_gateway_path_from_receipt(receipt_path: str, expected: str) -> None:
    assert gateway_path_from_receipt(receipt_path) == expected


def _runtime_agent_with_file_write_planner(workspace: Path) -> tuple[AgentLoop, Path]:
    reg = _registry(workspace)
    llm = FakeLLM(responses=["[synthesised]"])
    planner = FakePlanner(
        [
            {
                "tool": "file_write",
                "arguments": {"path": "runtime_gateway_skip.txt", "content": "nope"},
                "label": "file_write:runtime_skip",
                "expected_outcome": "writes file",
            }
        ]
    )
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        clarification_enabled=False,
    )
    return agent, log_path


@pytest.mark.parametrize("receipt_path", ["runtime", "daemon"])
def test_runtime_dry_run_goal_simulates_file_write(
    workspace: Path, receipt_path: str
) -> None:
    agent, log_path = _runtime_agent_with_file_write_planner(workspace)
    runtime = AutonomousRuntime(
        agent, workspace=workspace, receipt_path=receipt_path  # type: ignore[arg-type]
    )
    task = AutonomousTask(kind="goal", description="write runtime_gateway_skip.txt")
    report = runtime._task_goal(task, AutonomousRuntimeConfig(dry_run=True))
    assert report.status == "done"
    assert not (workspace / "runtime_gateway_skip.txt").exists()
    parsed = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        e.get("event") == "gateway_decision"
        and e.get("payload", {}).get("outcome") == "simulate"
        and e.get("payload", {}).get("path") == receipt_path
        for e in parsed
    )


class _ActiveKillSwitch:
    def status(self, snapshot=None, *, now_iso=None):
        from core.budget_kill_switch import KillSwitchState

        return KillSwitchState(
            active=True,
            reason="budget_kill_switch",
            counter="llm_calls",
            window="day",
            used=100,
            limit=100,
        )


def test_evaluate_block_on_active_kill_switch(workspace: Path) -> None:
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    gw = ActuationGateway(
        policy,
        path="repl",
        dry_run=False,
        kill_switch=_ActiveKillSwitch(),
    )
    action = Action(
        step_id="s1",
        type="tool_call",
        tool_name="file_write",
        parameters={"path": "blocked.txt", "content": "x"},
    )
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "block"
    assert any("kill_switch_active" in r for r in decision.reasons)


def test_evaluate_readiness_block_before_policy(workspace: Path) -> None:
    reg = _registry(workspace)
    policy = PolicyGate(reg)
    gw = ActuationGateway(
        policy,
        path="runtime",
        dry_run=False,
        check_readiness=True,
        readiness_blockers=("2 approval item(s) pending",),
    )
    action = Action(
        step_id="s1",
        type="tool_call",
        tool_name="file_write",
        parameters={"path": "blocked.txt", "content": "x"},
    )
    decision = gw.evaluate(action, registry=reg)
    assert decision.outcome == "block"
    assert any("readiness_blocker" in r for r in decision.reasons)


def test_evaluate_self_apply_block_on_kill_switch() -> None:
    gw = ActuationGateway(
        policy=None,
        path="self_apply",
        dry_run=False,
        kill_switch=_ActiveKillSwitch(),
        check_readiness=True,
    )
    decision = gw.evaluate_self_apply()
    assert decision.outcome == "block"
