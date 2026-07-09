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


# --------------------------------------------------------------------------- #
# validation rejection branches — load-bearing "refuse a bad proposal" guards   #
# --------------------------------------------------------------------------- #

def test_generator_rejects_missing_target_file(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response(target_file="")]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("target_file must be a non-empty string" in w for w in report.warnings)


def test_generator_rejects_empty_proposed_content(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response(proposed_content="   ")]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("proposed_content must be a non-empty string" in w for w in report.warnings)


def test_generator_rejects_invalid_confidence(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response(confidence="very-high")]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("confidence must be a number" in w for w in report.warnings)


def test_generator_rejects_missing_evidence(workspace: Path, monkeypatch):
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response(evidence=[])]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("evidence must contain at least one string" in w for w in report.warnings)


def test_generator_rejects_empty_diff(workspace: Path, monkeypatch):
    # proposed_content identical to current file → zero-line diff → refuse.
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[
            _json_response(proposed_content="def answer():\n    return 41\n")
        ]),
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("empty diff" in w for w in report.warnings)


def test_generator_rejects_oversized_diff(workspace: Path, monkeypatch):
    # A patch that changes more lines than max_changed_lines is refused so a
    # "repair" can never quietly rewrite a whole file.
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    big = "def answer():\n" + "".join(f"    x{i} = {i}\n" for i in range(20)) + "    return 42\n"
    generator = RepairProposalGenerator(
        workspace_root=workspace,
        llm=FakeLLM(responses=[_json_response(proposed_content=big)]),
        max_changed_lines=2,
    )

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "rejected"
    assert report.proposal is None
    assert any("too many lines" in w for w in report.warnings)


def test_generator_reports_tool_error_when_baseline_run_fails(workspace: Path, monkeypatch):
    # If the baseline test run itself raises, the generator must NOT invent a
    # repair on top of an unknown baseline — it returns tool_error.
    _seed(workspace)

    def boom(argv, **kwargs):
        raise OSError("pytest binary missing")

    monkeypatch.setattr(subprocess, "run", boom)
    llm = FakeLLM(responses=[_json_response()])
    generator = RepairProposalGenerator(workspace_root=workspace, llm=llm)

    report = generator.generate(target_path="buggy.py", test_paths=("tests",))

    assert report.status == "tool_error"
    assert report.proposal is None
    # The LLM was never consulted — no baseline, no proposal.
    assert llm.calls == []


def test_generator_uses_trace_id_diagnostic_logs_in_prompt(workspace: Path, monkeypatch):
    # When a trace_id is supplied the generator pulls diagnostic logs and folds
    # them into the LLM prompt; a valid proposal still comes back.
    _seed(workspace)
    _fake_pytest(monkeypatch, passed=False)
    trace_id = new_trace_id()
    (workspace / "logs").mkdir(exist_ok=True)
    (workspace / "logs" / f"{trace_id}.jsonl").write_text(
        json.dumps({"event": "error", "trace_id": trace_id, "payload": {"message": "boom"}}) + "\n",
        encoding="utf-8",
    )
    llm = FakeLLM(responses=[_json_response()])
    generator = RepairProposalGenerator(workspace_root=workspace, llm=llm)

    report = generator.generate(
        target_path="buggy.py", test_paths=("tests",), trace_id=trace_id
    )

    assert report.status == "proposed"
    assert report.proposal is not None
    assert report.diagnostic_logs is not None
    assert llm.calls  # the single proposal prompt was built and sent


def test_generator_rejects_invalid_constructor_args(workspace: Path):
    # Guard: non-directory workspace and non-positive bounds are refused up
    # front so a misconfigured generator can never run.
    import pytest

    with pytest.raises(ValueError):
        RepairProposalGenerator(workspace_root=workspace / "nope", llm=FakeLLM(responses=[]))
    with pytest.raises(ValueError):
        RepairProposalGenerator(
            workspace_root=workspace, llm=FakeLLM(responses=[]), max_context_chars=0
        )
    with pytest.raises(ValueError):
        RepairProposalGenerator(
            workspace_root=workspace, llm=FakeLLM(responses=[]), max_changed_lines=0
        )


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
