"""C16 — the only producer arc the autonomous contour has, bitten through the tick.

The contour's census (2026-08-14) found EXACTLY ONE call site in the repository
that puts a task into the queue: ``core/scheduler.py:217``, inside
``SchedulerStore.tick()``, converting a due schedule into a task. Everything the
unattended agent ever does arrives through this one wire.

The wire had a unit test and no observer at the tick.
``test_scheduler.py::test_tick_enqueues_due_tasks_and_advances_schedule`` drives
``SchedulerStore.tick`` directly, against two paths the test itself invents. It
proves the store's arithmetic. It cannot see whether ``run_tick`` calls it at
all, and it cannot see whether the task lands in the store the SAME tick then
drains — which is the failure this contour actually had (a task pending in one
store, invisible to the process meant to run it).

Every existing tick test starts from a task already in the queue
(``test_agent_tick_task_lifecycle.py`` seeds it with ``queue.add`` before calling
``run_tick``), so none of them enters through a schedule.

Measured, M61 (2026-08-14): severing the wire in ``run_tick`` — replacing
``sched_store.tick(task_queue=task_store)`` with an empty report — left the whole
suite green, 7603 passed. A due schedule produced nothing, the tick ran no work,
and not one test anywhere noticed.

The two halves are separate assertions because they are separate facts:

* the schedule became a task **and the runtime was actually reached with its
  goal** — the probe sits at the consumer, not at the parameter (nerve protocol
  point 8: prove real data arrived, not that a function has an argument);
* the queue row this tick created reached a terminal status, so the producer and
  the drain met inside one tick rather than in two different stores.

The schedule is written through ``app.bootstrap``'s constant and read by the
tick through its own ``agent_tick.SCHEDULES_PATH``. Deliberately not the same
symbol: if those two ever name different files again, this test is what goes
red.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import agent_tick
from app.bootstrap import DEFAULT_RUNTIME_SCHEDULES_PATH
from core.autonomous_runtime import AutonomousRunReport
from core.scheduler import SchedulerStore
from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH, TaskQueueStore

GOAL = "c16 producer probe"


def _completed_report(goal: str) -> AutonomousRunReport:
    return AutonomousRunReport(
        status="completed",   # type: ignore[arg-type]
        dry_run=True,
        goal=goal,
        tasks=[],
        budget={},
        circuit={},
        approvals={},
        stop_reason="",
    )


def test_a_due_schedule_becomes_work_the_same_tick_runs(workspace: Path, monkeypatch):
    """The contour's one producer, end to end: schedule -> task -> runtime."""
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

    import core.autonomous_runtime as ar
    from app import bootstrap

    goals_reached: list[str] = []

    def _run(self, config):
        goals_reached.append(config.goal)
        return _completed_report(config.goal)

    monkeypatch.setattr(bootstrap, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(ar.AutonomousRuntime, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(ar.AutonomousRuntime, "run", _run)
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    # The contour starts exactly as the live workspace does: a schedule exists,
    # and the queue is empty. Nothing is pre-seeded into the queue — that is the
    # whole point, since every other tick test starts on the far side of this arc.
    now = datetime.now(timezone.utc)
    SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH).add(
        name="c16-probe",
        goal=GOAL,
        every_minutes=30,
        start_at=now - timedelta(minutes=5),
        dry_run=True,
        include_tests=False,
        limit=1,
    )
    queue = TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH)
    assert queue.pending() == [], "the queue must be empty before the tick"

    agent_tick.run_tick(workspace, dry_run=True)

    assert goals_reached == [GOAL], (
        "a due schedule did not reach the runtime: the contour's only producer "
        f"delivered {goals_reached!r}"
    )
    rows = queue.load()
    assert len(rows) == 1, f"expected exactly the scheduled task, got {rows!r}"
    assert rows[0].goal == GOAL
    assert rows[0].status == "done", (
        "the task this tick created did not reach a terminal status in the same "
        f"tick — producer and drain did not meet (status={rows[0].status!r})"
    )


def test_a_schedule_not_yet_due_produces_no_work(workspace: Path, monkeypatch):
    """The anti-vacuity pole: the arc must carry a distinction, not always fire.

    Without this, an implementation that enqueued every schedule on every tick
    would pass the test above and be badly wrong.
    """
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

    import core.autonomous_runtime as ar
    from app import bootstrap

    goals_reached: list[str] = []

    def _run(self, config):
        goals_reached.append(config.goal)
        return _completed_report(config.goal)

    monkeypatch.setattr(bootstrap, "build_agent", lambda *a, **k: object())
    monkeypatch.setattr(ar.AutonomousRuntime, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(ar.AutonomousRuntime, "run", _run)
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    now = datetime.now(timezone.utc)
    SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH).add(
        name="c16-probe-future",
        goal=GOAL,
        every_minutes=30,
        start_at=now + timedelta(hours=6),
        dry_run=True,
        include_tests=False,
        limit=1,
    )

    agent_tick.run_tick(workspace, dry_run=True)

    assert goals_reached == [], "a schedule that is not due reached the runtime"
    assert TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH).load() == []
