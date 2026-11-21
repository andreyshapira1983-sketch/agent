"""Gateway slices G1–G2 — actuation gateway unit + path integration tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.actuation_gateway import (
    ActuationGateway,
    gateway_path_from_receipt,
)
from core.approval import AutoApprover
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
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
