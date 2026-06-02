"""Tests for core/subagent_memory_scope.py — MVP-18.1 Autonomous Subagent Proposal."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from core.subagent_memory_scope import (
    BudgetScope,
    MemoryScope,
    SubagentProposal,
    SubagentProposalResult,
    ToolScope,
    make_default_proposal,
    needs_delegation,
    propose_subagent,
)


# ── helpers ────────────────────────────────────────────────────────────────────

class _FakeLLM:
    """Minimal LLM stub that returns a canned string."""

    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, *, system: str, user: str, max_tokens: int = 512,
                 temperature: float = 0.0, **kwargs: Any) -> str:
        return self._response


def _good_llm_response(*, needed: bool = True) -> str:
    """Return a valid JSON proposal or not-needed response."""
    if not needed:
        return json.dumps({"needed": False, "reason": "task is trivial"})
    return json.dumps({
        "needed": True,
        "why_needed": "Task requires background monitoring",
        "proposed_role": "UpworkScanner",
        "memory_read_tags": ["project", "goals"],
        "memory_write_tags": ["upwork_findings"],
        "write_requires_review": True,
        "allowed_tools": ["web_search", "file_read"],
        "forbidden_tools": ["shell_exec"],
        "read_only": False,
        "max_model_calls": 4,
        "max_web_fetches": 10,
        "max_file_writes": 0,
        "max_cycles": 2,
        "risk_level": "medium",
        "expected_output": "Ranked list of Upwork opportunities",
        "approval_required": True,
    })


# ── MemoryScope ────────────────────────────────────────────────────────────────

def test_memory_scope_defaults():
    ms = MemoryScope(read_tags=("a",), write_tags=())
    assert ms.write_requires_review is True


def test_memory_scope_to_dict():
    ms = MemoryScope(read_tags=("project", "fact"), write_tags=("notes",))
    d = ms.to_dict()
    assert d["read_tags"] == ["project", "fact"]
    assert d["write_tags"] == ["notes"]
    assert d["write_requires_review"] is True


def test_memory_scope_frozen():
    ms = MemoryScope(read_tags=("a",), write_tags=())
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ms.read_tags = ("b",)  # type: ignore[misc]


def test_memory_scope_empty_tags():
    ms = MemoryScope(read_tags=(), write_tags=())
    assert ms.to_dict()["read_tags"] == []
    assert ms.to_dict()["write_tags"] == []


# ── ToolScope ─────────────────────────────────────────────────────────────────

def test_tool_scope_defaults():
    ts = ToolScope(allowed_tools=("file_read",), forbidden_tools=())
    assert ts.read_only is True


def test_tool_scope_to_dict():
    ts = ToolScope(allowed_tools=("web_search",), forbidden_tools=("shell_exec",))
    d = ts.to_dict()
    assert "web_search" in d["allowed_tools"]
    assert "shell_exec" in d["forbidden_tools"]
    assert d["read_only"] is True


def test_tool_scope_overlap_raises():
    with pytest.raises(ValueError, match="both allowed and forbidden"):
        ToolScope(
            allowed_tools=("web_search", "shell_exec"),
            forbidden_tools=("shell_exec",),
        )


def test_tool_scope_frozen():
    ts = ToolScope(allowed_tools=(), forbidden_tools=())
    with pytest.raises((FrozenInstanceError, AttributeError)):
        ts.read_only = False  # type: ignore[misc]


# ── BudgetScope ───────────────────────────────────────────────────────────────

def test_budget_scope_defaults():
    bs = BudgetScope()
    assert bs.max_model_calls == 3
    assert bs.max_web_fetches == 5
    assert bs.max_file_writes == 0
    assert bs.max_cycles == 1


def test_budget_scope_custom():
    bs = BudgetScope(max_model_calls=10, max_web_fetches=20, max_file_writes=5, max_cycles=3)
    d = bs.to_dict()
    assert d["max_model_calls"] == 10
    assert d["max_file_writes"] == 5


def test_budget_scope_negative_raises():
    with pytest.raises(ValueError):
        BudgetScope(max_model_calls=-1)


def test_budget_scope_zero_allowed():
    bs = BudgetScope(max_model_calls=0, max_web_fetches=0, max_file_writes=0, max_cycles=0)
    assert bs.max_model_calls == 0


def test_budget_scope_frozen():
    bs = BudgetScope()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        bs.max_cycles = 99  # type: ignore[misc]


# ── SubagentProposal ──────────────────────────────────────────────────────────

def _make_proposal(**overrides: Any) -> SubagentProposal:
    defaults = dict(
        task_goal="monitor Upwork",
        why_needed="background task",
        proposed_role="UpworkScanner",
        memory_scope=MemoryScope(read_tags=("project",), write_tags=()),
        tool_scope=ToolScope(allowed_tools=("web_search",), forbidden_tools=("shell_exec",)),
        budget_scope=BudgetScope(),
        risk_level="low",
        expected_output="List of opportunities",
    )
    defaults.update(overrides)
    return SubagentProposal(**defaults)


def test_proposal_defaults():
    p = _make_proposal()
    assert p.approval_required is True
    assert "sap" in p.proposal_id
    assert p.created_at != ""


def test_proposal_frozen():
    p = _make_proposal()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        p.task_goal = "changed"  # type: ignore[misc]


def test_proposal_to_dict_keys():
    p = _make_proposal()
    d = p.to_dict()
    for key in (
        "proposal_id", "task_goal", "why_needed", "proposed_role",
        "memory_scope", "tool_scope", "budget_scope",
        "risk_level", "expected_output", "approval_required", "created_at",
    ):
        assert key in d, f"missing key: {key}"


def test_proposal_to_dict_nested():
    p = _make_proposal()
    d = p.to_dict()
    assert isinstance(d["memory_scope"], dict)
    assert isinstance(d["tool_scope"], dict)
    assert isinstance(d["budget_scope"], dict)


def test_proposal_to_dict_json_serializable():
    p = _make_proposal()
    raw = json.dumps(p.to_dict())
    parsed = json.loads(raw)
    assert parsed["task_goal"] == "monitor Upwork"


def test_proposal_user_summary_contains_role():
    p = _make_proposal(proposed_role="UpworkScanner")
    s = p.user_summary()
    assert "UpworkScanner" in s
    assert "Предлагаемый подагент" in s


def test_proposal_user_summary_contains_risk():
    p = _make_proposal(risk_level="high")
    assert "high" in p.user_summary()


# ── SubagentProposalResult ────────────────────────────────────────────────────

def test_result_ok_proposed():
    r = SubagentProposalResult(status="proposed", proposal=_make_proposal())
    assert r.ok is True


def test_result_ok_llm_error():
    r = SubagentProposalResult(status="llm_error")
    assert r.ok is False


def test_result_ok_not_needed():
    r = SubagentProposalResult(status="not_needed")
    assert r.ok is False


def test_result_to_dict():
    r = SubagentProposalResult(
        status="proposed",
        proposal=_make_proposal(),
        reason="delegation needed",
        warnings=["w1"],
    )
    d = r.to_dict()
    assert d["status"] == "proposed"
    assert d["proposal"] is not None
    assert d["warnings"] == ["w1"]


def test_result_user_summary_status():
    r = SubagentProposalResult(status="not_needed", reason="trivial task")
    s = r.user_summary()
    assert "Делегация не нужна" in s
    assert "trivial" in s


def test_result_user_summary_with_proposal():
    r = SubagentProposalResult(status="proposed", proposal=_make_proposal())
    s = r.user_summary()
    assert "Предлагаемый подагент" in s


# ── needs_delegation ──────────────────────────────────────────────────────────

def test_needs_delegation_false_for_simple_goal():
    assert needs_delegation("summarise the project") is False


def test_needs_delegation_true_monitor():
    assert needs_delegation("monitor Upwork tasks every hour") is True


def test_needs_delegation_true_batch():
    assert needs_delegation("batch process all sources") is True


def test_needs_delegation_true_russian():
    assert needs_delegation("периодически следи за новостями") is True


def test_needs_delegation_empty_string():
    assert needs_delegation("") is False


def test_needs_delegation_non_string():
    # should not raise — just return False
    assert needs_delegation(None) is False  # type: ignore[arg-type]


# ── make_default_proposal ─────────────────────────────────────────────────────

def test_make_default_proposal_fields():
    p = make_default_proposal("run status check")
    assert p.task_goal == "run status check"
    assert p.risk_level == "low"
    assert p.approval_required is True
    assert p.tool_scope.read_only is True
    assert p.budget_scope.max_file_writes == 0


def test_make_default_proposal_serializable():
    p = make_default_proposal("check logs")
    raw = json.dumps(p.to_dict())
    assert json.loads(raw)["task_goal"] == "check logs"


# ── propose_subagent ──────────────────────────────────────────────────────────

def test_propose_subagent_llm_error_on_invalid_json():
    llm = _FakeLLM("This is not JSON at all.")
    result = propose_subagent("find tasks", llm=llm)
    assert result.status == "llm_error"
    assert result.proposal is None
    assert result.ok is False


def test_propose_subagent_not_needed():
    llm = _FakeLLM(_good_llm_response(needed=False))
    result = propose_subagent("say hello", llm=llm)
    assert result.status == "not_needed"
    assert result.proposal is None
    assert "trivial" in result.reason


def test_propose_subagent_proposed():
    llm = _FakeLLM(_good_llm_response(needed=True))
    result = propose_subagent("monitor Upwork", llm=llm)
    assert result.status == "proposed"
    assert result.ok is True
    assert result.proposal is not None
    assert result.proposal.proposed_role == "UpworkScanner"


def test_propose_subagent_risk_level_set():
    llm = _FakeLLM(_good_llm_response(needed=True))
    result = propose_subagent("monitor Upwork", llm=llm)
    assert result.proposal.risk_level == "medium"  # type: ignore[union-attr]


def test_propose_subagent_invalid_risk_level_defaults_low():
    raw = json.dumps({
        "needed": True,
        "why_needed": "reasons",
        "proposed_role": "TestAgent",
        "memory_read_tags": [],
        "memory_write_tags": [],
        "allowed_tools": ["file_read"],
        "forbidden_tools": [],
        "max_model_calls": 1,
        "max_web_fetches": 1,
        "max_file_writes": 0,
        "max_cycles": 1,
        "risk_level": "critical",  # invalid
        "expected_output": "output",
        "approval_required": True,
    })
    llm = _FakeLLM(raw)
    result = propose_subagent("something", llm=llm)
    assert result.ok
    assert result.proposal.risk_level == "low"  # type: ignore[union-attr]


def test_propose_subagent_overlap_tools_resolved():
    """If LLM puts same tool in both allowed and forbidden, allowed is stripped."""
    raw = json.dumps({
        "needed": True,
        "why_needed": "x",
        "proposed_role": "R",
        "memory_read_tags": [],
        "memory_write_tags": [],
        "allowed_tools": ["web_search", "shell_exec"],  # shell_exec also forbidden
        "forbidden_tools": ["shell_exec"],
        "max_model_calls": 1,
        "max_web_fetches": 1,
        "max_file_writes": 0,
        "max_cycles": 1,
        "risk_level": "low",
        "expected_output": "x",
        "approval_required": True,
    })
    llm = _FakeLLM(raw)
    result = propose_subagent("x", llm=llm)
    assert result.ok
    assert "shell_exec" not in result.proposal.tool_scope.allowed_tools  # type: ignore[union-attr]
    assert "web_search" in result.proposal.tool_scope.allowed_tools  # type: ignore[union-attr]


def test_propose_subagent_approval_required_default():
    llm = _FakeLLM(_good_llm_response(needed=True))
    result = propose_subagent("something", llm=llm)
    assert result.proposal.approval_required is True  # type: ignore[union-attr]


def test_propose_subagent_with_logger():
    events: list[str] = []

    class _Logger:
        def log(self, event: str, payload: Any) -> None:
            events.append(event)

    llm = _FakeLLM(_good_llm_response(needed=True))
    propose_subagent("task", llm=llm, logger=_Logger())
    assert "subagent_proposal_start" in events
    assert "subagent_proposal_ready" in events


def test_propose_subagent_raw_response_preserved():
    raw = _good_llm_response(needed=False)
    llm = _FakeLLM(raw)
    result = propose_subagent("quick task", llm=llm)
    assert result.raw_response == raw


def test_propose_subagent_budget_scope_from_llm():
    llm = _FakeLLM(_good_llm_response(needed=True))
    result = propose_subagent("monitor Upwork", llm=llm)
    assert result.proposal.budget_scope.max_model_calls == 4  # type: ignore[union-attr]
    assert result.proposal.budget_scope.max_web_fetches == 10  # type: ignore[union-attr]
