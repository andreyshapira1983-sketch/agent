"""Tests for core/subagent_memory_scope.py — MVP-18.1 Autonomous Subagent Proposal."""
from __future__ import annotations

import json
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


# ── ToolScope ─────────────────────────────────────────────────────────────────


def test_tool_scope_overlap_raises():
    with pytest.raises(ValueError, match="both allowed and forbidden"):
        ToolScope(
            allowed_tools=("web_search", "shell_exec"),
            forbidden_tools=("shell_exec",),
        )


# ── BudgetScope ───────────────────────────────────────────────────────────────


def test_budget_scope_negative_raises():
    with pytest.raises(ValueError):
        BudgetScope(max_model_calls=-1)


# ── SubagentProposal ──────────────────────────────────────────────────────────

def _make_proposal(**overrides: Any) -> SubagentProposal:
    defaults = {
        "task_goal": "monitor Upwork",
        "why_needed": "background task",
        "proposed_role": "UpworkScanner",
        "memory_scope": MemoryScope(read_tags=("project",), write_tags=()),
        "tool_scope": ToolScope(allowed_tools=("web_search",), forbidden_tools=("shell_exec",)),
        "budget_scope": BudgetScope(),
        "risk_level": "low",
        "expected_output": "List of opportunities",
    }
    defaults.update(overrides)
    return SubagentProposal(**defaults)


def test_proposal_user_summary_contains_risk():
    p = _make_proposal(risk_level="high")
    assert "high" in p.user_summary()


# ── SubagentProposalResult ────────────────────────────────────────────────────


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


def test_needs_delegation_true_batch():
    assert needs_delegation("batch process all sources") is True


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


def test_propose_subagent_approval_required_default():
    """An LLM answer that omits approval_required still yields a proposal needing approval."""
    data = json.loads(_good_llm_response(needed=True))
    del data["approval_required"]
    result = propose_subagent("something", llm=_FakeLLM(json.dumps(data)))
    assert result.ok
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
