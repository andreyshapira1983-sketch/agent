"""Tests for the read-only self-build supervisor cycle.

Covers the pure decision logic in ``core.self_build_supervisor`` and the CLI
handler wiring in ``main``. No real LLM/provider/network calls are made, and the
supervisor must never invoke file_write/shell_exec/run_tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.self_build_supervisor import (
    evaluate_self_build_supervisor,
    hour_budget_headroom,
    is_budget_near_exhaustion,
)


def _budget_windows(*, llm_used: int, llm_limit: int, tok_used: int, tok_limit: int) -> dict:
    return {
        "path": "data/budget_ledger.jsonl",
        "windows": [
            {
                "name": "hour",
                "seconds": 3600,
                "counters": {
                    "llm_calls": {"used": llm_used, "limit": llm_limit},
                    "model_tokens": {"used": tok_used, "limit": tok_limit},
                    # Unconfigured counter must never gate a cycle.
                    "cycles": {"used": 99, "limit": 0},
                },
            },
            {
                "name": "day",
                "seconds": 86400,
                "counters": {
                    "llm_calls": {"used": 1, "limit": 200},
                },
            },
        ],
    }


_HEALTHY = _budget_windows(llm_used=2, llm_limit=24, tok_used=1000, tok_limit=160000)


class _Flag:
    """Candidate provider that records whether it was invoked."""

    def __init__(self, payload: dict | None):
        self.payload = payload
        self.called = False

    def __call__(self) -> dict:
        self.called = True
        if self.payload is None:  # pragma: no cover - only used to prove non-call
            raise AssertionError("candidate_provider must not be called while waiting")
        return self.payload


# --- pure headroom / near-exhaustion -------------------------------------------------


def test_hour_headroom_skips_unconfigured_counters():
    headroom = hour_budget_headroom(_HEALTHY)
    assert "cycles" not in headroom  # limit == 0
    assert headroom["llm_calls"]["headroom"] == 22
    assert headroom["model_tokens"]["limit"] == 160000


def test_near_exhaustion_on_low_llm_calls():
    windows = _budget_windows(llm_used=22, llm_limit=24, tok_used=1000, tok_limit=160000)
    near, reasons = is_budget_near_exhaustion(hour_budget_headroom(windows))
    assert near is True
    assert any("llm_calls" in r for r in reasons)


def test_near_exhaustion_on_low_token_ratio():
    windows = _budget_windows(llm_used=1, llm_limit=24, tok_used=150000, tok_limit=160000)
    near, reasons = is_budget_near_exhaustion(hour_budget_headroom(windows))
    assert near is True
    assert any("model_tokens" in r for r in reasons)


def test_healthy_budget_is_not_near_exhaustion():
    near, reasons = is_budget_near_exhaustion(hour_budget_headroom(_HEALTHY))
    assert near is False
    assert reasons == []


# --- evaluator gating ----------------------------------------------------------------


def test_budget_wait_does_not_call_candidate_provider():
    windows = _budget_windows(llm_used=23, llm_limit=24, tok_used=1000, tok_limit=160000)
    provider = _Flag(None)  # would raise if called

    report = evaluate_self_build_supervisor(
        budget_windows=windows,
        approvals_pending=0,
        candidate_provider=provider,
    )

    assert report["status"] == "budget_wait"
    assert report["candidate"] is None
    assert provider.called is False
    assert report["checked_sections"] == ["budget"]


def test_approval_wait_does_not_call_candidate_provider():
    provider = _Flag(None)  # would raise if called

    report = evaluate_self_build_supervisor(
        budget_windows=_HEALTHY,
        approvals_pending=3,
        candidate_provider=provider,
    )

    assert report["status"] == "approval_wait"
    assert report["candidate"] is None
    assert provider.called is False
    assert "approvals" in report["checked_sections"]
    assert "candidate" not in report["checked_sections"]


def test_normal_state_returns_one_candidate():
    payload = {
        "diagnosis": "example",
        "file": "core/operator_intent.py",
        "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        "tests": "pytest",
        "risk": "low",
    }
    provider = _Flag(payload)

    report = evaluate_self_build_supervisor(
        budget_windows=_HEALTHY,
        approvals_pending=0,
        task_queue={"pending_due": 0},
        scheduler={"due": 0},
        recent_errors=["planner_error", "run_tests_failed"],
        tech_debt={"present": True, "open": 2, "done": 10},
        candidate_provider=provider,
    )

    assert report["status"] == "ok"
    assert provider.called is True
    assert report["candidate"] == payload
    assert set(report["checked_sections"]) >= {
        "budget",
        "approvals",
        "scheduler",
        "task_queue",
        "recent_errors",
        "tech_debt",
        "candidate",
    }
    assert report["evidence"]["recent_error_count"] == 2


def test_normal_state_no_patch_is_ok():
    payload = {"file": "core/operator_intent.py", "diff": "NO_PATCH"}
    provider = _Flag(payload)

    report = evaluate_self_build_supervisor(
        budget_windows=_HEALTHY,
        approvals_pending=0,
        candidate_provider=provider,
    )

    assert report["status"] == "ok"
    assert report["candidate"] == "NO_PATCH"
    assert provider.called is True


def test_non_budget_context_is_serialisable_and_log_safe():
    payload = {"file": "core/operator_intent.py", "diff": "NO_PATCH"}
    report = evaluate_self_build_supervisor(
        budget_windows=_HEALTHY,
        approvals_pending=0,
        candidate_provider=lambda: payload,
    )
    import json

    # Report must round-trip through JSON (used by --json and structured logging).
    assert json.loads(json.dumps(report)) == report


# --- CLI handler wiring --------------------------------------------------------------


def test_cli_command_registered_and_no_effects(monkeypatch, tmp_path):
    """The :self-build-supervisor command is registered, runs read-only, and
    never invokes file_write/shell_exec/run_tests or any LLM/provider call."""
    import main as main_module
    from main import handle_meta_command

    # Reuse the real local agent builder from the CLI test suite.
    from tests.test_cli import _build_agent  # type: ignore

    workspace = tmp_path
    (workspace / "data").mkdir(parents=True, exist_ok=True)
    agent = _build_agent(workspace)

    # Guard: candidate provider is the only place analysis happens; make sure the
    # supervisor never reaches for effectful tools.
    def _boom(*_args, **_kwargs):  # pragma: no cover - only fires on misuse
        raise AssertionError("supervisor must not run effectful tools")

    effectful = {"file_write", "shell_exec", "run_tests"}
    for tool in agent.registry.list():
        if tool.name in effectful:
            monkeypatch.setattr(tool, "run", _boom, raising=False)

    assert handle_meta_command(":self-build-supervisor", agent, workspace) is True
    assert handle_meta_command(":self-build-supervisor --json", agent, workspace) is True

    # No LLM/provider calls, and a structured event was logged.
    assert len(agent.llm.calls) == 0
    log_text = agent.log.path.read_text(encoding="utf-8")
    assert "model_call_start" not in log_text
    assert "self_build_supervisor" in log_text
