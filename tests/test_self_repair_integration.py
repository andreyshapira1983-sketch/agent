"""MVP-13.1 — integration tests for self-repair diagnostic primitives.

These tests drive the three new tools through the full AgentLoop +
PolicyGate + AutoApprover stack so we pin behaviour at the same level
the user actually exercises (i.e. nothing relies on a tool being
called directly).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.self_repair import RepairProposal
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM, FakePlanner


# ============================================================
# Helpers
# ============================================================

def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_agent(
    workspace: Path,
    *,
    canned_sources: list[dict],
    approval_default: str | None = "approve",
) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace, timeout_seconds=5.0))
    reg.register(RunTestsTool(workspace_root=workspace, timeout_seconds=10.0))
    reg.register(ReadLogsTool(workspace_root=workspace))
    reg.register(DiffFileTool(workspace_root=workspace))

    for src in canned_sources:
        src.setdefault("expected_outcome", "executes the planned step")

    llm = FakeLLM(responses=["[synthesised answer]"])
    planner = FakePlanner(canned_sources)

    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    approver = AutoApprover(default=approval_default) if approval_default else None

    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=approver,
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# run_tests — full integration
# ============================================================

class TestRunTestsIntegration:
    def test_run_tests_runs_without_approval(self, workspace: Path, monkeypatch):
        """run_tests is `reversible` — PolicyGate allows + audits, no
        approval prompt. We deliberately do NOT require approval here
        so the agent can prove its work in the self-repair loop without
        a modal at every step. The full self-repair CONTROLLER (MVP-13.2)
        wraps the chain in a single approval."""
        def fake_run(argv, **kwargs):
            class C:
                returncode = 0
                stdout = b"1 passed in 0.01s\n"
                stderr = b""
            return C()

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "run_tests",
                "arguments": {"paths": ["tests"]},
                "label": "run_tests:tests",
            }],
        )
        agent.run("run my tests")
        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # No approval cycle for reversible action.
        assert "approval_request" not in kinds
        # Policy event was emitted with `reversible` audit reason.
        policy_events = [e for e in ev if e["event"] == "policy"]
        assert policy_events
        reasons = policy_events[-1]["payload"].get("reasons", [])
        assert any("reversible" in r for r in reasons)
        # Tool executed.
        assert "tool_result" in kinds

    def test_run_tests_audit_carries_argv(self, workspace: Path, monkeypatch):
        """The audit log must show what was actually invoked. Critical
        for after-the-fact diagnosis of an agent's test runs."""
        captured: list[list[str]] = []

        def fake_run(argv, **kwargs):
            captured.append(list(argv))
            class C:
                returncode = 0
                stdout = b"1 passed in 0.01s\n"
                stderr = b""
            return C()

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "run_tests",
                "arguments": {"paths": ["tests"], "pattern": "memory"},
                "label": "run_tests:tests",
            }],
        )
        agent.run("run memory tests")
        assert captured, "subprocess.run was never called"
        argv = captured[-1]
        assert "-k" in argv and argv[argv.index("-k") + 1] == "memory"
        # The audit log carries the same argv.
        result_events = [e for e in _events(log_path) if e["event"] == "tool_result"]
        out = result_events[-1]["payload"]["output"]
        assert "-k" in out["command"]
        assert "memory" in out["command"]

    def test_run_tests_failed_tests_in_result(self, workspace: Path, monkeypatch):
        """A red suite must round-trip through the loop with structured
        failure data the planner can read."""
        def fake_run(argv, **kwargs):
            class C:
                returncode = 1
                stdout = (
                    b"FAILED tests/test_x.py::test_one\n"
                    b"1 failed in 0.01s\n"
                )
                stderr = b""
            return C()

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "run_tests",
                "arguments": {"paths": ["tests"]},
                "label": "run_tests:tests",
            }],
        )
        agent.run("run my tests")
        ev = _events(log_path)
        result_events = [e for e in ev if e["event"] == "tool_result"]
        assert result_events
        payload = result_events[-1]["payload"]
        output = payload["output"]
        assert output["failed"] == 1
        assert "tests/test_x.py::test_one" in output["failed_tests"]


# ============================================================
# read_logs — full integration
# ============================================================

class TestReadLogsIntegration:
    def test_read_logs_no_approval_needed(self, workspace: Path):
        # Pre-seed a log file the tool can read.
        (workspace / "logs").mkdir(exist_ok=True)
        seed = workspace / "logs" / "run_seed.jsonl"
        seed.write_text(json.dumps({
            "ts": "2026-01-01T00:00:00Z",
            "trace_id": "run_seed",
            "event": "planner",
            "payload": {},
        }) + "\n", encoding="utf-8")

        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "read_logs",
                "arguments": {"trace_id": "run_seed", "last_n": 10},
                "label": "read_logs:run_seed",
            }],
        )
        agent.run("show recent logs")
        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # read_only -> no approval cycle.
        assert "approval_request" not in kinds
        # Tool ran successfully.
        result_events = [e for e in ev if e["event"] == "tool_result"]
        assert result_events
        out = result_events[-1]["payload"]["output"]
        assert out["trace_id"] == "run_seed"
        assert out["events_returned"] >= 1

    def test_read_logs_event_filter(self, workspace: Path):
        log_dir = workspace / "logs"
        log_dir.mkdir(exist_ok=True)
        seed = log_dir / "run_mixed.jsonl"
        seed.write_text(
            json.dumps({"trace_id": "x", "event": "planner"}) + "\n" +
            json.dumps({"trace_id": "x", "event": "error"}) + "\n" +
            json.dumps({"trace_id": "x", "event": "respond"}) + "\n",
            encoding="utf-8",
        )
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "read_logs",
                "arguments": {
                    "trace_id": "run_mixed",
                    "event_filter": ["error"],
                    "last_n": 50,
                },
                "label": "read_logs:run_mixed",
            }],
        )
        agent.run("find error events")
        ev = _events(log_path)
        result_events = [e for e in ev if e["event"] == "tool_result"]
        assert result_events
        out = result_events[-1]["payload"]["output"]
        assert out["events_returned"] == 1
        assert out["events"][0]["event"] == "error"


# ============================================================
# diff_file — full integration
# ============================================================

class TestDiffFileIntegration:
    def test_diff_file_runs_without_approval(self, workspace: Path):
        target = workspace / "self.py"
        target.write_text("def foo():\n    return 1\n", encoding="utf-8")
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "diff_file",
                "arguments": {
                    "path": "self.py",
                    "proposed_content": "def foo():\n    return 42\n",
                    "context_lines": 3,
                },
                "label": "diff_file:self.py",
            }],
        )
        agent.run("preview the change")
        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # read_only -> no approval.
        assert "approval_request" not in kinds
        result_events = [e for e in ev if e["event"] == "tool_result"]
        out = result_events[-1]["payload"]["output"]
        assert out["additions"] == 1
        assert out["deletions"] == 1
        assert "return 42" in out["diff"]
        # diff_file did NOT write to disk: the file content is unchanged.
        assert target.read_text(encoding="utf-8") == "def foo():\n    return 1\n"

    def test_diff_file_for_new_file_shows_additions(self, workspace: Path):
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "diff_file",
                "arguments": {
                    "path": "new_module.py",
                    "proposed_content": "x = 1\ny = 2\n",
                },
                "label": "diff_file:new_module.py",
            }],
        )
        agent.run("preview new file")
        ev = _events(log_path)
        result_events = [e for e in ev if e["event"] == "tool_result"]
        out = result_events[-1]["payload"]["output"]
        assert out["file_exists"] is False
        assert out["additions"] == 2


# ============================================================
# Self-repair chain — diff THEN write THEN tests
# ============================================================

class TestSelfRepairChain:
    """Pin the canonical sequence: diff_file -> (review) -> file_write
    -> run_tests. This is the skeleton MVP-13.2 will turn into a real
    controller; for now we just prove every tool composes in one plan."""

    def test_diff_then_write_then_run_tests(self, workspace: Path, monkeypatch):
        target = workspace / "module.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        def fake_run(argv, **kwargs):
            class C:
                returncode = 0
                stdout = b"3 passed in 0.05s\n"
                stderr = b""
            return C()

        monkeypatch.setattr(subprocess, "run", fake_run)

        agent, log_path = _build_agent(
            workspace,
            canned_sources=[
                {
                    "tool": "diff_file",
                    "arguments": {
                        "path": "module.py",
                        "proposed_content": "VALUE = 42\n",
                    },
                    "label": "diff_file:module.py",
                },
                {
                    "tool": "file_write",
                    "arguments": {
                        "path": "module.py",
                        "content": "VALUE = 42\n",
                    },
                    "label": "file_write:module.py",
                },
                {
                    "tool": "run_tests",
                    "arguments": {"paths": ["tests"]},
                    "label": "run_tests:tests",
                },
            ],
        )
        agent.run("propose a value change, write it, verify")
        ev = _events(log_path)
        tool_names = [
            e["payload"]["tool_name"]
            for e in ev
            if e["event"] == "tool_call"
        ]
        # AgentLoop runs the first artifact-producing step and stops; we
        # at least verify the sequencing surface — diff_file ran first.
        assert tool_names[0] == "diff_file"
        assert target.read_text(encoding="utf-8") in (
            "VALUE = 1\n", "VALUE = 42\n"
        )


# ============================================================
# Self-repair controller — diagnose -> diff -> approval -> write -> tests
# ============================================================

class TestSelfRepairController:
    def test_repair_success_writes_after_approval_and_green_tests(
        self,
        workspace: Path,
        monkeypatch,
    ):
        target = workspace / "module.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        _fake_pytest_sequence(
            monkeypatch,
            [
                (1, b"1 failed, 2 passed in 0.05s\n"),
                (0, b"3 passed in 0.05s\n"),
            ],
        )
        agent, log_path = _build_agent(workspace, canned_sources=[])

        report = agent.repair(
            RepairProposal(
                path="module.py",
                proposed_content="VALUE = 42\n",
                test_paths=("tests",),
                reason="test-driven repair",
            ),
            workspace_root=workspace,
        )

        assert report.status == "repaired"
        assert target.read_text(encoding="utf-8") == "VALUE = 42\n"
        assert len(agent.approval_provider.calls) == 1
        assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
            "run_tests",
            "diff_file",
            "file_write",
            "run_tests",
        ]
        assert any(e["event"] == "self_repair_result" for e in _events(log_path))

    def test_repair_rolls_back_when_post_tests_fail(
        self,
        workspace: Path,
        monkeypatch,
    ):
        target = workspace / "module.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        _fake_pytest_sequence(
            monkeypatch,
            [
                (1, b"1 failed, 2 passed in 0.05s\n"),
                (1, b"1 failed, 2 passed in 0.05s\n"),
            ],
        )
        agent, _ = _build_agent(workspace, canned_sources=[])

        report = agent.repair(
            RepairProposal(
                path="module.py",
                proposed_content="VALUE = 42\n",
                test_paths=("tests",),
            ),
            workspace_root=workspace,
        )

        assert report.status == "rolled_back"
        assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert report.rollback_summary is not None
        assert report.rollback_summary["error"] == 0
        assert agent.compensation_log == []

    def test_repair_denied_by_approval_does_not_write(
        self,
        workspace: Path,
        monkeypatch,
    ):
        target = workspace / "module.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        _fake_pytest_sequence(monkeypatch, [(1, b"1 failed in 0.05s\n")])
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[],
            approval_default="deny",
        )

        report = agent.repair(
            RepairProposal(
                path="module.py",
                proposed_content="VALUE = 42\n",
            ),
            workspace_root=workspace,
        )

        assert report.status == "approval_denied"
        assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert [e["payload"]["tool_name"] for e in _events(log_path) if e["event"] == "tool_call"] == [
            "run_tests",
            "diff_file",
        ]

    def test_repair_no_changes_stops_before_approval_or_write(
        self,
        workspace: Path,
        monkeypatch,
    ):
        target = workspace / "module.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        _fake_pytest_sequence(monkeypatch, [(1, b"1 failed in 0.05s\n")])
        agent, _ = _build_agent(workspace, canned_sources=[])

        report = agent.repair(
            RepairProposal(
                path="module.py",
                proposed_content="VALUE = 1\n",
            ),
            workspace_root=workspace,
        )

        assert report.status == "no_changes"
        assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert agent.approval_provider.calls == []

    def test_low_confidence_proposal_blocks_before_approval_or_write(
        self,
        workspace: Path,
        monkeypatch,
    ):
        target = workspace / "module.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        _fake_pytest_sequence(monkeypatch, [(1, b"1 failed in 0.05s\n")])
        agent, log_path = _build_agent(workspace, canned_sources=[])

        report = agent.repair(
            RepairProposal(
                path="module.py",
                proposed_content="VALUE = 42\n",
                confidence=0.40,
                evidence=("failing test observed",),
            ),
            workspace_root=workspace,
        )

        assert report.status == "low_confidence"
        assert "confidence=0.40" in report.user_summary()
        assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert agent.approval_provider.calls == []
        events = _events(log_path)
        assert [e["payload"]["tool_name"] for e in events if e["event"] == "tool_call"] == [
            "run_tests",
            "diff_file",
        ]
        assert any(e["event"] == "self_repair_confidence_gate" for e in events)


def _fake_pytest_sequence(monkeypatch, outcomes: list[tuple[int, bytes]]) -> None:
    queue = list(outcomes)

    def fake_run(argv, **kwargs):
        assert queue, "unexpected extra pytest invocation"
        returncode, stdout = queue.pop(0)

        class C:
            stderr = b""

            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        return C(returncode, stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
