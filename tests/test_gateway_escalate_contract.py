"""Gateway slice G5c — escalate path contract (repl + runtime).

Proves effectful tools follow: gateway → escalate → approval → invoke (or stop).
Mostly regression tests; no behavior change unless audit finds a gap.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.approval import AutoApprover
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig, AutonomousTask
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.tool_receipts import ToolReceiptLedger, default_receipts_path
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.shell_exec import ShellExecTool


def _seed_budget_config(workspace: Path) -> None:
    """Enable persistent budget enforcement so runtime readiness consult passes."""
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("budget_limits.json").write_text(
        json.dumps(
            {
                "windows": {
                    "hour": {"llm_calls": 10, "model_cost_units": 20},
                    "day": {"llm_calls": 100, "model_cost_units": 200},
                }
            }
        ),
        encoding="utf-8",
    )


def _events(log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _gateway_receipts(workspace: Path, *, operation: str = "gateway.escalate") -> list:
    return [
        r
        for r in ToolReceiptLedger(default_receipts_path(workspace)).load()
        if r.kind == "gateway" and r.operation == operation
    ]


def _build_repl_agent(
    workspace: Path,
    *,
    planned_step: dict[str, Any],
    approval_default: str,
) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    registry.register(ShellExecTool(workspace_root=workspace))
    planner = FakePlanner(sources=[planned_step])
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["done"]),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default=approval_default),  # type: ignore[arg-type]
        max_replan_attempts=1,
        clarification_enabled=False,
        gateway_dry_run=False,
        gateway_path="repl",
    )
    return agent, log_path


def _overwrite_step(path: str = "doc.txt", content: str = "updated\n") -> dict[str, Any]:
    return {
        "tool": "file_write",
        "arguments": {"path": path, "content": content},
        "label": f"file_write:{path}",
        "expected_outcome": "overwrite file",
    }


def _shell_touch_step() -> dict[str, Any]:
    return {
        "tool": "shell_exec",
        "arguments": {"argv": ["touch", "touched.txt"]},
        "label": "shell_exec:touch",
        "expected_outcome": "create touched.txt",
    }


def test_repl_file_write_overwrite_escalate_deny_no_invoke(workspace: Path) -> None:
    target = workspace / "doc.txt"
    target.write_text("original\n", encoding="utf-8")

    agent, log_path = _build_repl_agent(
        workspace,
        planned_step=_overwrite_step(),
        approval_default="deny",
    )
    agent.run("overwrite doc.txt")

    assert target.read_text(encoding="utf-8") == "original\n"
    events = _events(log_path)
    names = [e["event"] for e in events]
    assert "gateway_decision" in names
    assert names.index("gateway_decision") < names.index("approval_request")
    assert "tool_call" not in names
    assert "tool_result" not in names

    gw = next(e for e in events if e["event"] == "gateway_decision")
    assert gw["payload"]["outcome"] == "escalate"
    assert gw["payload"]["tool_name"] == "file_write"

    escalate_rows = _gateway_receipts(workspace)
    assert len(escalate_rows) == 1
    assert escalate_rows[0].path == "repl"
    assert escalate_rows[0].refs.get("tool_name") == "file_write"


def test_repl_file_write_overwrite_escalate_approve_invokes_after_approval(
    workspace: Path,
) -> None:
    target = workspace / "doc.txt"
    target.write_text("original\n", encoding="utf-8")

    agent, log_path = _build_repl_agent(
        workspace,
        planned_step=_overwrite_step(),
        approval_default="approve",
    )
    agent.run("overwrite doc.txt")

    assert target.read_text(encoding="utf-8") == "updated\n"
    events = _events(log_path)
    names = [e["event"] for e in events]
    idx = {
        n: names.index(n)
        for n in ("gateway_decision", "approval_request", "approval_decision", "tool_call")
    }
    assert idx["gateway_decision"] < idx["approval_request"] < idx["approval_decision"] < idx["tool_call"]
    assert _gateway_receipts(workspace)


def test_repl_shell_exec_escalate_deny_no_invoke(workspace: Path) -> None:
    agent, log_path = _build_repl_agent(
        workspace,
        planned_step=_shell_touch_step(),
        approval_default="deny",
    )
    agent.run("touch a file")

    assert not (workspace / "touched.txt").exists()
    events = _events(log_path)
    names = [e["event"] for e in events]
    assert "gateway_decision" in names
    gw = next(e for e in events if e["event"] == "gateway_decision")
    assert gw["payload"]["outcome"] == "escalate"
    assert gw["payload"]["tool_name"] == "shell_exec"
    assert "tool_call" not in names
    assert _gateway_receipts(workspace)


def _runtime_agent_for_overwrite(
    workspace: Path,
    *,
    approval_default: str,
    receipt_path: str,
) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["done"]),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[_overwrite_step(path="runtime_doc.txt")]),
        approval_provider=AutoApprover(default=approval_default),  # type: ignore[arg-type]
        max_replan_attempts=1,
        clarification_enabled=False,
        gateway_dry_run=False,
        gateway_path=receipt_path,  # type: ignore[arg-type]
    )
    return agent, log_path


@pytest.mark.parametrize("receipt_path", ["runtime", "daemon"])
def test_runtime_goal_escalate_deny_no_invoke(
    workspace: Path, receipt_path: str
) -> None:
    target = workspace / "runtime_doc.txt"
    target.write_text("keep me\n", encoding="utf-8")
    _seed_budget_config(workspace)

    agent, log_path = _runtime_agent_for_overwrite(
        workspace,
        approval_default="deny",
        receipt_path=receipt_path,
    )
    runtime = AutonomousRuntime(
        agent,
        workspace=workspace,
        receipt_path=receipt_path,  # type: ignore[arg-type]
    )
    report = runtime._task_goal(
        AutonomousTask(kind="goal", description="overwrite runtime_doc.txt"),
        AutonomousRuntimeConfig(dry_run=False, limit=1, include_tests=False),
    )

    assert report.status == "clarify"
    assert report.details.get("stop_reason") == "replan_exhausted"
    assert target.read_text(encoding="utf-8") == "keep me\n"
    events = _events(log_path)
    names = [e["event"] for e in events]
    assert "gateway_decision" in names
    gw = next(e for e in events if e["event"] == "gateway_decision")
    assert gw["payload"]["outcome"] == "escalate"
    assert gw["payload"]["path"] == receipt_path
    assert "tool_call" not in names

    escalate_rows = _gateway_receipts(workspace)
    assert len(escalate_rows) == 1
    assert escalate_rows[0].path == receipt_path
