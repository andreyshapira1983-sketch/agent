"""Regression tests for the daemon auto-repair path (`_maybe_propose_repair`).

These pin the exact runtime bug seen in `logs/daemon_tick.jsonl`: when a tick
recorded failing tests, the auto-repair safety net raised
``RepairProposalGenerator.__init__() got an unexpected keyword argument
'workspace'`` and proposed nothing — the one mechanism that fires on breakage
was itself broken. A second latent defect passed ``target_path=None`` to a
``generate`` that requires a concrete ``str`` target.

The contract verified here:
  - failing tests → the generator is constructed with ``workspace_root`` (no
    TypeError) and ``generate`` receives a valid existing ``target_path`` str;
  - when no single concrete target can be derived, the path refuses cleanly
    with an understandable status instead of crashing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent_tick
from agent_tick import _maybe_propose_repair, _repair_target_from_failures
from core.self_repair import RepairProposal


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #

class _FakeReport:
    """Mirrors the parts of ProposalGenerationReport that the daemon reads."""

    def __init__(self, status, proposal=None):
        self.status = status
        self.proposal = proposal
        self.confidence = 0.9
        self.evidence = ("ev1", "ev2")
        self.diagnosis = "diag"


class _FakeGen:
    """Records construction kwargs + the target passed to generate."""

    last_init: dict | None = None
    last_target: str | None = None
    report: _FakeReport = _FakeReport("no_failing_tests")

    def __init__(self, **kwargs):
        _FakeGen.last_init = kwargs

    def generate(self, *, target_path, **kwargs):
        _FakeGen.last_target = target_path
        return _FakeGen.report


class _FakeInbox:
    def __init__(self):
        self.added: list[dict] = []

    def add(self, **kwargs):
        self.added.append(kwargs)


class _FakeAgent:
    llm = object()


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeGen.last_init = None
    _FakeGen.last_target = None
    _FakeGen.report = _FakeReport("no_failing_tests")
    yield


def _patch_gen(monkeypatch):
    monkeypatch.setattr(
        "core.repair_proposal.RepairProposalGenerator", _FakeGen, raising=True
    )


# --------------------------------------------------------------------------- #
# _repair_target_from_failures                                                 #
# --------------------------------------------------------------------------- #

def test_single_existing_failing_file_is_the_target(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")
    target = _repair_target_from_failures(
        ["tests/test_x.py::test_a", "tests/test_x.py::test_b"], tmp_path
    )
    assert target == "tests/test_x.py"


def test_multiple_distinct_files_yield_no_target(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")
    (tmp_path / "tests" / "test_y.py").write_text("# y\n", encoding="utf-8")
    target = _repair_target_from_failures(
        ["tests/test_x.py::test_a", "tests/test_y.py::test_b"], tmp_path
    )
    assert target is None


def test_nonexistent_file_yields_no_target(tmp_path: Path):
    target = _repair_target_from_failures(["tests/test_ghost.py::test_a"], tmp_path)
    assert target is None


# --------------------------------------------------------------------------- #
# _maybe_propose_repair — the actual bug                                       #
# --------------------------------------------------------------------------- #

def test_no_failures_short_circuits(tmp_path: Path):
    result = _maybe_propose_repair(
        tmp_path, {"failed": 0, "errors": 0}, _FakeInbox(), _FakeAgent()
    )
    assert result == {"repair_proposed": False, "reason": "all tests passed"}


def test_generator_constructed_with_workspace_root_not_workspace(tmp_path, monkeypatch):
    """The bug: constructor was called with `workspace=` → TypeError.

    The generator must now be built with `workspace_root=` and `generate`
    must receive a concrete existing target_path (never None).
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")
    _patch_gen(monkeypatch)

    _maybe_propose_repair(
        tmp_path,
        {"failed": 1, "errors": 0, "failed_tests": ["tests/test_x.py::test_a"]},
        _FakeInbox(),
        _FakeAgent(),
    )

    assert _FakeGen.last_init is not None
    assert "workspace" not in _FakeGen.last_init  # the old broken kwarg
    assert _FakeGen.last_init.get("workspace_root") == tmp_path
    assert _FakeGen.last_target == "tests/test_x.py"  # a real str, not None


def test_proposed_report_is_inboxed(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")
    # Use the REAL RepairProposal so the test is bound to its actual field
    # contract (path/reason/proposed_content) — a fake with invented field
    # names is exactly what let a shape-mismatch bug slip past before.
    real_proposal = RepairProposal(
        path="tests/test_x.py",
        proposed_content="# fixed\n",
        reason="fix the thing",
        confidence=0.9,
    )
    _FakeGen.report = _FakeReport("proposed", real_proposal)
    _patch_gen(monkeypatch)
    inbox = _FakeInbox()

    result = _maybe_propose_repair(
        tmp_path,
        {"failed": 1, "errors": 0, "failed_tests": ["tests/test_x.py::test_a"]},
        inbox,
        _FakeAgent(),
    )

    assert result["repair_proposed"] is True
    assert result["target"] == "tests/test_x.py"
    assert len(inbox.added) == 1
    assert inbox.added[0]["operation"] == "repair_proposal"
    assert inbox.added[0]["payload"]["target_file"] == "tests/test_x.py"


def test_underivable_target_refuses_cleanly_without_building_generator(tmp_path, monkeypatch):
    """No single concrete target → clean refusal, generator never constructed."""
    _patch_gen(monkeypatch)

    result = _maybe_propose_repair(
        tmp_path,
        {"failed": 2, "errors": 0,
         "failed_tests": ["tests/test_ghost.py::test_a", "tests/test_phantom.py::test_b"]},
        _FakeInbox(),
        _FakeAgent(),
    )

    assert result["repair_proposed"] is False
    assert "could not determine a single repair target" in result["reason"]
    assert _FakeGen.last_init is None  # never reached the generator


def test_no_llm_on_agent_refuses_cleanly(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("# x\n", encoding="utf-8")
    _patch_gen(monkeypatch)

    class _NoLLM:
        llm = None

    result = _maybe_propose_repair(
        tmp_path,
        {"failed": 1, "errors": 0, "failed_tests": ["tests/test_x.py::test_a"]},
        _FakeInbox(),
        _NoLLM(),
    )
    assert result == {"repair_proposed": False, "reason": "no llm on agent"}
