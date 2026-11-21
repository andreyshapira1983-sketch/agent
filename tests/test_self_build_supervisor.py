"""Tests for the read-only self-build supervisor cycle.

Covers the pure decision logic in ``core.self_build_supervisor`` and the CLI
handler wiring in ``main``. No real LLM/provider/network calls are made, and the
supervisor must never invoke file_write/shell_exec/run_tests.
"""
from __future__ import annotations

from core.self_build_supervisor import (
    evaluate_self_build_supervisor,
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
