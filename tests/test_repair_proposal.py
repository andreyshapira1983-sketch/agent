"""MVP-13.3 — Repair Proposal Generator tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.repair_proposal import RepairProposalGenerator
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def _seed(workspace: Path) -> None:
    (workspace / "tests").mkdir()
    (workspace / "buggy.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    (workspace / "tests" / "test_buggy.py").write_text(
        "from buggy import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )


def _json_response(**overrides) -> str:
    data = {
        "diagnosis": "answer() returns 41 while the failing test expects 42",
        "target_file": "buggy.py",
        "proposed_content": "def answer():\n    return 42\n",
        "evidence": ["tests/test_buggy.py::test_answer failed", "buggy.py contains return 41"],
        "confidence": 0.82,
    }
    data.update(overrides)
    return json.dumps(data)


def _fake_pytest(monkeypatch, *, passed: bool) -> None:
    def fake_run(argv, **kwargs):
        class C:
            returncode = 0 if passed else 1
            stdout = b"1 passed in 0.01s\n" if passed else b"1 failed in 0.01s\nFAILED tests/test_buggy.py::test_answer - AssertionError\n"
            stderr = b""

        return C()

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_generator_creates_valid_repair_proposal(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response()]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.ok
    assert report.status == "proposed"
    assert report.proposal is not None
    assert report.proposal.path == "buggy.py"
    assert report.proposal.proposed_content == "def answer():\n    return 42\n"
    assert report.proposal.confidence == 0.82
    assert report.proposal.evidence == (
        "tests/test_buggy.py::test_answer failed",
        "buggy.py contains return 41",
    )
    assert report.confidence == 0.82
    assert report.diff_preview is not None
    assert report.diff_preview["additions"] == 1
    assert report.diff_preview["deletions"] == 1
    assert report.summary()["dry_run"] is True
    assert "dry_run=True" in report.user_summary()


def test_generator_refuses_when_baseline_tests_are_green(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=True)
    llm = FakeLLM(responses=[_json_response()])
    generator = RepairProposalGenerator(workspace_root=workspace, llm=llm)

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "no_failing_tests"
    assert report.proposal is None
    assert llm.calls == []


def test_generator_rejects_wrong_target_file(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response(target_file="other.py")]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("does not match requested target" in w for w in report.warnings)


def test_generator_rejects_secret_in_proposed_content(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[
            _json_response(proposed_content="OPENAI_API_KEY=sk-" + "a" * 32 + "\n")
        ]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("secret material" in w for w in report.warnings)


def test_generator_rejects_invalid_json(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=["not json"]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "llm_error"
    assert report.proposal is None


def test_agent_loop_exposes_propose_repair(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    llm = FakeLLM(responses=[_json_response()])
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner([]),
        approval_provider=AutoApprover(),
        max_replan_attempts=1,
    )

    report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=workspace,
        test_paths=("tests",),
    )

    assert report.ok
    assert report.proposal is not None
    assert (workspace / "buggy.py").read_text(encoding="utf-8") == "def answer():\n    return 41\n"
