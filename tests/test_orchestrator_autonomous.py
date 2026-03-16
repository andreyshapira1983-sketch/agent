"""
Tests for autonomous Orchestrator: observe, reason, plan, act, reflect, improve, run_cycle.
"""
from __future__ import annotations

from unittest.mock import patch

import src.tools  # noqa: F401 — register tools so run_tool has get_current_time
from src.tools.orchestrator import Orchestrator, run_tool, _is_successful_tool_result
from src.governance.policy_engine import PolicyEngine


def test_run_tool_still_works():
    out = run_tool("get_current_time")
    assert isinstance(out, str)
    assert "Tool error" not in out or "202" in out or ":" in out


def test_run_tool_unknown_returns_error():
    out = run_tool("__nonexistent_tool__")
    assert isinstance(out, str)
    assert "Tool error" in out


def test_tool_result_classifier_detects_known_failures():
    assert _is_successful_tool_result("Written 10 chars to foo.py") is True
    assert _is_successful_tool_result("Patch submitted: abc123") is True
    assert _is_successful_tool_result("Error: file not found") is False
    assert _is_successful_tool_result("Cannot read file: missing") is False
    assert _is_successful_tool_result("Validation failed in sandbox") is False


def test_tool_result_classifier_strict_mode_kpi(monkeypatch):
    monkeypatch.setenv("AGENT_STRICT_MODE", "1")
    assert _is_successful_tool_result(
        "Diff saved to config/pending_patches/abc.patch\n--- DIFF ---\n--- src/tests/test_finance_manager.py",
        tool_name="request_patch",
        arguments={"path": "src/tests/test_finance_manager.py"},
    ) is True
    assert _is_successful_tool_result(
        "Diff saved to config/pending_patches/abc.patch\n--- DIFF ---\n--- tests/test_finance_manager.py",
        tool_name="request_patch",
        arguments={"path": "src/tests/test_finance_manager.py"},
    ) is False
    assert _is_successful_tool_result(
        "Written 100 chars to src/foo.py (sandbox tests passed; backup: src/foo.py.bak)",
        tool_name="write_file",
        arguments={"path": "src/foo.py"},
    ) is True
    assert _is_successful_tool_result(
        "Written 100 chars to src/foo.py",
        tool_name="write_file",
        arguments={"path": "src/foo.py"},
    ) is False


def test_orchestrator_observe():
    orch = Orchestrator()
    state = orch.observe()
    assert "metrics" in state
    assert "self_assessment" in state
    assert "sequence_trace" in state


def test_orchestrator_reason():
    orch = Orchestrator()
    state = {"metrics": {"errors": 2, "successes": 5}, "self_assessment": {"success_rate": 70.0}, "sequence_trace": []}
    goal = orch.reason(state)
    assert isinstance(goal, str)
    assert len(goal) > 0
    assert "error" in goal.lower() or "rate" in goal.lower()


def test_orchestrator_reason_zero_metrics():
    orch = Orchestrator()
    state = {"metrics": {"errors": 0, "successes": 0}, "self_assessment": {}, "sequence_trace": []}
    goal = orch.reason(state)
    assert "maintain" in goal.lower() or "quality" in goal.lower() or "gather" in goal.lower()


def test_orchestrator_reason_healthy():
    orch = Orchestrator()
    state = {"metrics": {"errors": 0, "successes": 10}, "self_assessment": {"success_rate": 95.0}, "sequence_trace": []}
    goal = orch.reason(state)
    assert "maintain" in goal.lower() or "monitor" in goal.lower() or "quality" in goal.lower()


def test_orchestrator_plan_enqueues():
    from src.tasks.queue import size, dequeue
    orch = Orchestrator()
    orch.plan("maintain quality")
    n = size()
    assert n >= 1
    while dequeue():
        pass


def test_orchestrator_reflect():
    orch = Orchestrator()
    summary = orch.reflect()
    assert "self_assessment" in summary
    assert "sequence_trace" in summary


def test_orchestrator_improve():
    orch = Orchestrator()
    out = orch.improve({"success": True})
    assert "adjusted" in out
    assert "improvements" in out


def test_orchestrator_run_cycle():
    policy = PolicyEngine(max_cycles=10, max_actions_per_cycle=5)
    orch = Orchestrator(policy=policy)
    summary = orch.run_cycle()
    assert summary["status"] in ("ok", "quota_exceeded")
    if summary["status"] == "ok":
        assert "goal" in summary
        assert "self_assessment" in summary


def test_orchestrator_run_cycle_quota_exceeded():
    policy = PolicyEngine(max_cycles=0)
    orch = Orchestrator(policy=policy)
    summary = orch.run_cycle()
    assert summary["status"] == "quota_exceeded"
    assert "message" in summary


def test_orchestrator_act_policy_denied():
    from src.tasks.queue import enqueue, dequeue, size
    from src.tasks.task_state import Task
    policy = PolicyEngine(max_actions_per_cycle=0)
    orch = Orchestrator(policy=policy)
    enqueue(Task("t1", {"tool": "get_current_time", "arguments": {}}))
    outcomes = orch.act()
    # When quota is 0, can_perform_action() is False so the act loop never runs — no outcomes, task stays queued
    assert len(outcomes) == 0
    assert size() == 1
    while dequeue():
        pass


def test_orchestrator_act_policy_denied_per_task():
    from src.tasks.queue import enqueue, dequeue
    from src.tasks.task_state import Task
    from src.governance import task_guard
    task_guard.advance_cycle()
    policy = PolicyEngine(max_actions_per_cycle=5)
    policy.check_run_tool = lambda name, args: (False, "denied by test")
    orch = Orchestrator(policy=policy)
    ok = enqueue(Task("t1", {"tool": "get_current_time", "arguments": {}}))
    assert ok, "enqueue should succeed after advance_cycle"
    outcomes = orch.act()
    while dequeue():
        pass
    assert len(outcomes) == 1
    assert outcomes[0].get("success") is False
    assert "denied" in outcomes[0].get("reason", "").lower()


def test_orchestrator_act_with_approval_skipped():
    from src.tasks.queue import enqueue, dequeue
    from src.tasks.task_state import Task
    from src.governance import task_guard
    task_guard.advance_cycle()
    with patch("src.agency.autonomy_manager.needs_confirmation", return_value=True):
        orch = Orchestrator(use_approval_layer=True)
        ok = enqueue(Task("t1", {"tool": "get_current_time", "arguments": {}}))
        assert ok
        outcomes = orch.act()
        while dequeue():
            pass
    assert len(outcomes) == 1
    assert outcomes[0].get("reason") == "pending_approval"


def test_orchestrator_act_marks_cannot_read_as_failure(monkeypatch):
    from src.tasks.queue import enqueue, dequeue
    from src.tasks.task_state import Task
    from src.governance import task_guard

    task_guard.advance_cycle()
    orch = Orchestrator(policy=PolicyEngine(max_actions_per_cycle=5))
    monkeypatch.setattr("src.tools.orchestrator.run_tool", lambda name, arguments=None: "Cannot read file: missing")
    ok = enqueue(Task("t1", {"tool": "request_patch", "arguments": {"path": "tests/test_finance_manager.py", "user_goal": "fix"}}))
    assert ok
    outcomes = orch.act()
    while dequeue():
        pass
    assert len(outcomes) == 1
    assert outcomes[0].get("success") is False


def test_orchestrator_improve_with_low_success():
    orch = Orchestrator()
    out = orch.improve({"success": False})
    assert "improvements" in out
    assert "adjusted" in out


def test_orchestrator_run_stops_on_quota():
    policy = PolicyEngine(max_cycles=2, max_actions_per_cycle=5)
    orch = Orchestrator(policy=policy)
    orch.run(max_cycles=5)
    assert policy._cycles_done == 2


def test_orchestrator_run_breaks_on_quota_exceeded():
    policy = PolicyEngine(max_cycles=0)
    orch = Orchestrator(policy=policy)
    orch.run(max_cycles=3)
    assert policy._cycles_done == 0


def test_orchestrator_run_with_check_alerts_every_n_cycles():
    policy = PolicyEngine(max_cycles=5, max_actions_per_cycle=5)
    orch = Orchestrator(policy=policy)
    orch.run(max_cycles=2, check_alerts_every_n_cycles=1)
    assert policy._cycles_done == 2
