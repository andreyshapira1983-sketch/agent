"""MVP-13.4 — self-repair end-to-end hardening.

These are the automated version of the manual live audit:

    diagnose -> propose-repair -> diff -> approval -> apply -> tests -> success/rollback

They use a real temporary workspace and real pytest subprocesses. The only
scripted part is the LLM response, so the safety pipeline stays deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakePlanner
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool


class ScriptLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.provider = "script"
        self.model = "script-repair-proposal"

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        self.calls.append({
            "system": system,
            "user": user,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        assert self.responses, "unexpected extra LLM call"
        return self.responses.pop(0)


def _proposal_json(*, return_value: int, confidence: float = 0.86) -> str:
    return json.dumps({
        "diagnosis": "answer() returns 41 while the test expects 42",
        "target_file": "buggy.py",
        "proposed_content": f"def answer():\n    return {return_value}\n",
        "evidence": [
            "tests/test_buggy.py::test_answer failed",
            "buggy.py currently returns 41",
        ],
        "confidence": confidence,
    })


def _seed_workspace(workspace: Path) -> None:
    (workspace / "tests").mkdir()
    (workspace / "buggy.py").write_text(
        "def answer():\n    return 41\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_buggy.py").write_text(
        "from buggy import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )


def _build_agent(
    workspace: Path,
    *,
    llm_response: str,
    approval_default: str | None,
    blocked_tools: frozenset[str] | None = None,
) -> tuple[AgentLoop, Path, AutoApprover | None]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace, timeout_seconds=5.0))
    reg.register(RunTestsTool(workspace_root=workspace, timeout_seconds=20.0))
    reg.register(ReadLogsTool(workspace_root=workspace))
    reg.register(DiffFileTool(workspace_root=workspace))

    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    # approval_default=None means "no approval provider configured at all" — the
    # repair controller must then refuse to apply a code change.
    approver = AutoApprover(default=approval_default) if approval_default is not None else None
    policy = PolicyGate(reg)
    if blocked_tools:
        policy.blocked_tools = frozenset(blocked_tools)
    agent = AgentLoop(
        registry=reg,
        policy=policy,
        llm=ScriptLLM([llm_response]),
        logger=logger,
        planner=FakePlanner([]),
        memory=WorkingMemory(),
        approval_provider=approver,
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl", approver


def _events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _diff_lines(diff_text: str) -> list[str]:
    return [
        line
        for line in diff_text.splitlines()
        if line.startswith(("---", "+++", "@@", "+", "-"))
    ]


def test_e2e_success_path_from_error_to_green_tests(tmp_path: Path):
    _seed_workspace(tmp_path)
    agent, log_path, approver = _build_agent(
        tmp_path,
        llm_response=_proposal_json(return_value=42),
        approval_default="approve",
    )
    initial = (tmp_path / "buggy.py").read_text(encoding="utf-8")

    proposal_report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    assert proposal_report.status == "proposed"
    assert proposal_report.baseline_tests["failed"] == 1
    assert proposal_report.summary()["dry_run"] is True
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == initial
    assert _diff_lines(proposal_report.diff_preview["diff"]) == [
        "--- a/buggy.py",
        "+++ b/buggy.py",
        "@@ -1,2 +1,2 @@",
        "-    return 41",
        "+    return 42",
    ]

    repair_report = agent.repair(proposal_report.proposal, workspace_root=tmp_path)

    assert repair_report.status == "repaired"
    assert repair_report.summary()["post_tests"]["passed"] == 1
    assert repair_report.summary()["approval"] == "approved"
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == "def answer():\n    return 42\n"
    assert len(approver.calls) == 1
    assert len(agent.compensation_log) == 1
    assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
        "run_tests",
        "diff_file",
        "file_write",
        "run_tests",
    ]


def test_e2e_approval_denial_leaves_file_untouched(tmp_path: Path):
    _seed_workspace(tmp_path)
    agent, log_path, approver = _build_agent(
        tmp_path,
        llm_response=_proposal_json(return_value=42),
        approval_default="deny",
    )
    initial = (tmp_path / "buggy.py").read_text(encoding="utf-8")
    proposal_report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    repair_report = agent.repair(proposal_report.proposal, workspace_root=tmp_path)

    assert repair_report.status == "approval_denied"
    assert repair_report.summary()["approval"] == "denied"
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == initial
    assert len(approver.calls) == 1
    assert len(agent.compensation_log) == 0
    assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
        "run_tests",
        "diff_file",
    ]


def test_e2e_bad_patch_rolls_back(tmp_path: Path):
    _seed_workspace(tmp_path)
    agent, log_path, approver = _build_agent(
        tmp_path,
        llm_response=_proposal_json(return_value=40, confidence=0.86),
        approval_default="approve",
    )
    initial = (tmp_path / "buggy.py").read_text(encoding="utf-8")
    proposal_report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    repair_report = agent.repair(proposal_report.proposal, workspace_root=tmp_path)

    assert repair_report.status == "rolled_back"
    assert repair_report.summary()["post_tests"]["failed"] == 1
    assert repair_report.rollback_summary["error"] == 0
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == initial
    assert len(approver.calls) == 1
    assert len(agent.compensation_log) == 0
    events = _events(log_path)
    assert any(e["event"] == "compensation_registered" for e in events)
    assert any(e["event"] == "compensation_apply" for e in events)


def test_e2e_low_confidence_blocks_before_approval(tmp_path: Path):
    _seed_workspace(tmp_path)
    agent, log_path, approver = _build_agent(
        tmp_path,
        llm_response=_proposal_json(return_value=42, confidence=0.30),
        approval_default="approve",
    )
    initial = (tmp_path / "buggy.py").read_text(encoding="utf-8")
    proposal_report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    repair_report = agent.repair(proposal_report.proposal, workspace_root=tmp_path)

    assert repair_report.status == "low_confidence"
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == initial
    assert len(approver.calls) == 0
    assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
        "run_tests",
        "diff_file",
    ]
    assert any(e["event"] == "self_repair_confidence_gate" for e in _events(log_path))


def test_e2e_no_approval_provider_refuses_to_apply(tmp_path: Path):
    """Safety brake: with NO approval provider configured, a self-repair that
    requires approval (APPLY_CODE_CHANGE) must refuse to touch the file.

    This is a load-bearing guard — the agent cannot rewrite its own code
    without an approval channel. It exercises the `provider is None` branch in
    `_request_approval` and the `approval_unavailable` mapping in run().
    """
    _seed_workspace(tmp_path)
    agent, log_path, approver = _build_agent(
        tmp_path,
        llm_response=_proposal_json(return_value=42),
        approval_default=None,  # no provider at all
    )
    assert approver is None
    initial = (tmp_path / "buggy.py").read_text(encoding="utf-8")
    proposal_report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    repair_report = agent.repair(proposal_report.proposal, workspace_root=tmp_path)

    assert repair_report.status == "approval_unavailable"
    # The file is byte-for-byte untouched and no compensation was registered.
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == initial
    assert len(agent.compensation_log) == 0
    # baseline + diff ran; the write never executed.
    assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
        "run_tests",
        "diff_file",
    ]
    assert any(
        e["event"] == "error"
        and e["payload"].get("code") == "approval_unavailable"
        for e in _events(log_path)
    )


def test_e2e_policy_blocked_write_tool_stops_repair_before_approval(tmp_path: Path):
    """Safety brake: if PolicyGate blocks `file_write`, the repair must stop at
    the write step — BEFORE any approval is even requested — and leave the file
    untouched. Exercises the `policy_decision.decision == "deny"` branch in
    `_execute_tool` and the default `blocked` mapping in `_blocked_status`.
    """
    _seed_workspace(tmp_path)
    agent, log_path, approver = _build_agent(
        tmp_path,
        llm_response=_proposal_json(return_value=42),
        approval_default="approve",
        blocked_tools=frozenset({"file_write"}),
    )
    initial = (tmp_path / "buggy.py").read_text(encoding="utf-8")
    proposal_report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    repair_report = agent.repair(proposal_report.proposal, workspace_root=tmp_path)

    assert repair_report.status == "blocked"
    assert (tmp_path / "buggy.py").read_text(encoding="utf-8") == initial
    # Denied at policy → approval was never consulted, nothing to roll back.
    assert approver is not None and len(approver.calls) == 0
    assert len(agent.compensation_log) == 0
    assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
        "run_tests",
        "diff_file",
    ]
    assert any(
        e["event"] == "error" and e["payload"].get("code") == "policy_blocked"
        for e in _events(log_path)
    )
