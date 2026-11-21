"""MIR-039 end-to-end: the cron consumer must record what actually happened.

Before this, ``agent_tick.run_tick`` called ``mark_done`` for every run it got
back and did not wrap the run at all:

* a run stopped by the budget circuit, or blocked awaiting an approval, was
  recorded as ``done`` — indistinguishable in the queue from work that finished;
* an exception out of ``runtime.run()`` escaped past the task handling to the
  outer error handler, which wrote a heartbeat and returned 1 **without
  touching the row**, leaving it ``running`` with nothing able to recover it.

These drive the real ``run_tick`` with a stubbed runtime and assert on the
durable queue row, not on log prose.
"""
from __future__ import annotations

import json
from pathlib import Path

import agent_tick
from core.autonomous_runtime import AutonomousRunReport
from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH, TaskQueueStore


def _report(status: str, stop_reason: str = "") -> AutonomousRunReport:
    return AutonomousRunReport(
        status=status,      # type: ignore[arg-type]
        dry_run=True,
        goal="project health",
        tasks=[],
        budget={},
        circuit={},
        approvals={},
        stop_reason=stop_reason,
    )


def _tick_with_runtime(workspace: Path, monkeypatch, run_impl) -> TaskQueueStore:
    """Run one real tick whose AutonomousRuntime.run is `run_impl`."""
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

    import core.autonomous_runtime as ar
    from app import bootstrap

    monkeypatch.setattr(bootstrap, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(ar.AutonomousRuntime, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(ar.AutonomousRuntime, "run", run_impl)
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    queue = TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH)
    queue.add(goal="project health", dry_run=True, include_tests=False, limit=1)

    agent_tick.run_tick(workspace, dry_run=True)
    return queue


def _tick_events(workspace: Path) -> list[dict]:
    log_path = workspace / "logs" / "daemon_tick.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_approval_blocked_run_parks_the_task(workspace: Path, monkeypatch):
    queue = _tick_with_runtime(
        workspace,
        monkeypatch,
        lambda self, config=None, **k: _report("blocked", "approval required: appr_3"),
    )

    task = queue.load()[0]
    assert task.status == "blocked"
    assert "appr_3" in task.last_error
    # Nothing will pick it up until a human clears it (`:task-unblock`).
    assert queue.pending() == []


def test_raising_run_does_not_leave_the_task_running(workspace: Path, monkeypatch):
    def _boom(self, config=None, **k):
        raise RuntimeError("provider exploded")

    queue = _tick_with_runtime(workspace, monkeypatch, _boom)

    task = queue.load()[0]
    assert task.status != "running", "a crashed run left the row claimed forever"
    assert task.status == "failed"
    assert "RuntimeError: provider exploded" in task.last_error

    events = {e.get("event") for e in _tick_events(workspace)}
    assert "task_failed" in events


def test_lock_is_released_so_the_next_tick_can_drain(workspace: Path, monkeypatch):
    """run_tick is also called in-process; a leaked lock would strand the queue."""
    queue = _tick_with_runtime(
        workspace, monkeypatch, lambda self, config=None, **k: _report("completed")
    )
    queue.add(goal="second", dry_run=True, include_tests=False, limit=1)

    agent_tick.run_tick(workspace, dry_run=True)

    assert [t.status for t in queue.load()] == ["done", "done"]
