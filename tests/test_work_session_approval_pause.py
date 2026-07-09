"""work-session must PAUSE (not fail) when a cycle blocks on human approval.

When the AutonomousRuntime returns ``status="blocked"`` with a
``stop_reason`` that starts with ``"approval required:"`` it means the agent
politely stopped and is waiting for a human to approve an irreversible action.
That is NOT a malfunction — it is the safety design working as intended.

The old behaviour treated every ``blocked``/``stopped`` cycle as a circuit
breaker *failure*. Two approval-blocked cycles in a row therefore exhausted the
failure budget and tripped the breaker, so the session ended with the scary
``stop_reason="circuit_open: failure budget exhausted: approval required: ..."``
even though nothing was actually broken.

Correct behaviour: an approval-blocked cycle stops the session immediately with
``stop_reason="awaiting_approval"`` and ``status="stopped"``, WITHOUT consuming
the circuit breaker's failure budget. Re-running would only re-block on the same
pending approval, so the session parks after the first such cycle.

A genuine failure (a ``stopped``/``blocked`` cycle whose reason is NOT an
approval request) must STILL count toward the failure budget and open the
circuit as before — the fix must not disable the breaker.

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


def test_approval_block_pauses_not_circuit_open(monkeypatch):
    """An approval-blocked cycle must park the session as 'awaiting_approval',
    not trip the circuit breaker."""
    agent = _FakeAgent()
    report = _run_report(
        "blocked", [], stop_reason="approval required: ain_abc123"
    )
    calls = _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="project health", max_cycles=3, minutes=60.0)
    result = run_work_session(config, agent=agent, workspace=None)

    assert result.stop_reason == "awaiting_approval"
    assert result.status == "stopped"
    assert "circuit_open" not in result.stop_reason
    # Parks on the FIRST approval-blocked cycle — re-running would only re-block
    # on the same pending approval.
    assert result.cycles_run == 1
    assert calls["count"] == 1


def test_awaiting_approval_event_logged(monkeypatch):
    """The pause must be recorded so an operator can see the session is waiting
    on them (and not mistake it for a crash)."""
    agent = _FakeAgent()
    report = _run_report(
        "blocked", [], stop_reason="approval required: ain_def456"
    )
    _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="project health", max_cycles=3, minutes=60.0)
    run_work_session(config, agent=agent, workspace=None)

    names = [e for (e, _p) in agent.log.events]
    assert "work_session_awaiting_approval" in names


def test_real_failure_still_opens_circuit(monkeypatch):
    """A non-approval failure must STILL count toward the failure budget and
    open the circuit — the approval-pause fix must not disable the breaker."""
    agent = _FakeAgent()
    report = _run_report(
        "stopped", [], stop_reason="queue budget or circuit stopped"
    )
    _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="project health", max_cycles=5, minutes=60.0)
    result = run_work_session(config, agent=agent, workspace=None)

    assert "circuit_open" in result.stop_reason
    assert result.status == "stopped"


def test_completed_cycle_unaffected(monkeypatch):
    """A normally completed cycle is unaffected by the approval-pause logic."""
    agent = _FakeAgent()
    report = _run_report(
        "completed",
        [SimpleNamespace(task=SimpleNamespace(kind="status"), status="done", summary="ok", details={})],
    )
    _install_fake_runtime(monkeypatch, [report])

    config = WorkSessionConfig(goal="project health", max_cycles=1, minutes=60.0)
    result = run_work_session(config, agent=agent, workspace=None)

    assert result.stop_reason != "awaiting_approval"
    assert result.status == "completed"
