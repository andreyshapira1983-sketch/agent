"""work-session early-stop on budget exhaustion.

When a cycle's goal task dies on a model/token budget limit
(ModelBudgetExceeded, surfaced by the runtime as a failed task carrying
error_type="ModelBudgetExceeded"), the work session must stop immediately with
stop_reason="budget_exhausted" instead of spinning the remaining max_cycles,
which would each hit the same exhausted budget window.

The AutonomousRuntime is faked here, so no real LLM/provider calls happen.
"""

from __future__ import annotations

from types import SimpleNamespace

import core.work_session as ws
from core.work_session import WorkSessionConfig, run_work_session


class _FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


class _FakeAgent:
    def __init__(self) -> None:
        self.log = _FakeLog()


def _task(kind: str, status: str, summary: str = "", error_type: str | None = None):
    details = {"error_type": error_type} if error_type else {}
    return SimpleNamespace(
        task=SimpleNamespace(kind=kind),
        status=status,
        summary=summary,
        details=details,
    )


def _run_report(status: str, tasks: list, stop_reason: str = ""):
    return SimpleNamespace(status=status, tasks=tasks, stop_reason=stop_reason)


def _install_fake_runtime(monkeypatch, reports: list) -> dict:
    """Patch core.work_session.AutonomousRuntime with a fake that returns the
    given reports one per cycle (repeating the last one if cycles outrun the
    list). Returns a dict tracking how many times .run() was called."""
    calls = {"count": 0}

    class _FakeRuntime:
        def __init__(self, agent, workspace=None, approval_inbox=None) -> None:
            self.agent = agent

        def run(self, config):
            idx = min(calls["count"], len(reports) - 1)
            calls["count"] += 1
            return reports[idx]

    monkeypatch.setattr(ws, "AutonomousRuntime", _FakeRuntime)
    return calls


def test_budget_exhausted_cycle_stops_session(monkeypatch):
    agent = _FakeAgent()
    report = _run_report(
        "completed",
        [
            _task("status", "done", "healthy"),
            _task(
                "goal",
                "failed",
                "ModelBudgetExceeded: hour token budget",
                error_type="ModelBudgetExceeded",
            ),
        ],
    )
    calls = _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="do work", max_cycles=3, minutes=60.0)
    result = run_work_session(config, agent=agent, workspace=None)

    assert result.stop_reason == "budget_exhausted"
    assert result.status == "stopped"
    assert result.cycles_run == 1
    # The session stopped after the first cycle, not spinning cycles 2 and 3.
    assert calls["count"] == 1


def test_normal_completed_cycle_does_not_budget_stop(monkeypatch):
    agent = _FakeAgent()
    report = _run_report(
        "completed",
        [
            _task("status", "done", "healthy"),
            _task("goal", "done", "did the thing"),
        ],
    )
    _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="do work", max_cycles=1, minutes=60.0)
    result = run_work_session(config, agent=agent, workspace=None)

    assert result.stop_reason != "budget_exhausted"
    assert result.status == "completed"


def test_non_budget_failure_does_not_budget_stop(monkeypatch):
    agent = _FakeAgent()
    report = _run_report(
        "completed",
        [
            _task("status", "done", "healthy"),
            _task("goal", "failed", "ValueError: boom", error_type="ValueError"),
        ],
    )
    _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="do work", max_cycles=1, minutes=60.0)
    result = run_work_session(config, agent=agent, workspace=None)

    assert result.stop_reason != "budget_exhausted"


def test_budget_exhausted_event_logged(monkeypatch):
    agent = _FakeAgent()
    report = _run_report(
        "completed",
        [
            _task(
                "goal",
                "failed",
                "ModelBudgetExceeded: hour token budget",
                error_type="ModelBudgetExceeded",
            ),
        ],
    )
    _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="do work", max_cycles=3, minutes=60.0)
    run_work_session(config, agent=agent, workspace=None)

    names = [e for (e, _p) in agent.log.events]
    assert "work_session_budget_exhausted" in names
    payload = next(p for (e, p) in agent.log.events if e == "work_session_budget_exhausted")
    assert payload["cycle"] == 1
    assert payload["goal"] == "do work"
