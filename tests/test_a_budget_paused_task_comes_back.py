"""Work parked by an exhausted budget must return to the queue by itself.

WHY THIS EXISTS. Measured on the live store 2026-08-22: fourteen tasks sit at
`status="paused"`, every one with `last_error="budget_exhausted"` and
`attempts=0/1`, the oldest since 2026-07-30. They are invisible to the
scheduler forever, because `pending()` returns only `status == "pending"` and
nothing anywhere moves a paused row back. `summary()` lists them under
`"resumable"` — a sensor with no actuator. That is why the unattended run of
2026-08-16 went quiet on its third day: it had not broken, it had run out of
work it was allowed to see.

THE DISTINCTION THAT IS THE WHOLE POINT. The queue already has a resting state
for "waiting on a human": `blocked`, whose own comment says retrying it on a
timer cannot help and which leaves by `unblock()` at the operator's word. That
is correct for a human decision. A budget-exhausted task waits on something
else entirely — a window that refills on a clock. Giving both the same
human-only exit is what strands the second kind. So the exit condition must
match the entry condition: parked by a resource, returned when the resource
returns.

WHAT THIS DOES NOT CLAIM. Not that every paused row should run again — a
checkpoint that has spent its attempts is terminal, exactly as MIR-040 taught
when an earlier recovery resurrected a task one attempt past its cap. Not that
`blocked` should change. And not that a resumed run will succeed.

THE GROWTH TRAP, pinned here because switching reactivation on without it would
turn a static fourteen into unbounded growth. A resume that hits the budget
again parks its own checkpoint, and `retire_paused_checkpoint` only fires on
success (`tests/test_budget_resume.py::test_resume_that_pauses_again_keeps_the_old_task`
holds that an unfinished pause may not be retired). So a re-park must carry the
SAME row forward rather than add a second one for the same work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.task_lifecycle import reactivate_resumable_work
from core.task_queue import TaskQueueStore


class _HeldLock:
    """The single-instance lock the startup recovery contract demands."""

    held = True


def _store(workspace: Path) -> TaskQueueStore:
    return TaskQueueStore(workspace / "data" / "runtime_tasks.jsonl")


def _park(store: TaskQueueStore, *, goal: str = "Answer the question: x",
          stop_reason: str = "budget_exhausted", trace_id: str = "tr-1"):
    return store.add_paused_checkpoint(
        goal=goal,
        report={"stop_reason": stop_reason, "trace_id": trace_id,
                "current_phase": "synthesis"},
    )


def test_a_budget_paused_checkpoint_returns_to_the_queue(workspace: Path) -> None:
    """The witness. Fourteen live rows are stranded on exactly this path."""
    store = _store(workspace)
    task = _park(store)
    assert store.pending() == [], "precondition: a parked row is not runnable"

    reactivate_resumable_work(store, lock=_HeldLock(), cooldown_minutes=0)

    runnable = {t.id for t in store.pending()}
    assert task.id in runnable, (
        "a task parked by an exhausted budget never returns to the queue; "
        "the scheduler cannot see it and no automatic path moves it"
    )


def test_a_blocked_task_is_left_alone(workspace: Path) -> None:
    """The boundary that must not move. `blocked` waits on a human decision,
    and a timer cannot supply one — its comment says so and MIR-039 earned it."""
    store = _store(workspace)
    task = store.add(goal="needs approval")
    store.mark_blocked(task.id, reason="awaiting operator")

    reactivate_resumable_work(store, lock=_HeldLock(), cooldown_minutes=0)

    assert store.get(task.id).status == "blocked"
    assert store.pending() == []


def test_a_checkpoint_that_spent_its_attempts_is_not_resurrected(
    workspace: Path,
) -> None:
    """MIR-040's lesson, applied to this path before it can repeat: recovery
    may not run a task one attempt past its cap."""
    store = _store(workspace)
    task = _park(store)
    store._update_one(  # noqa: SLF001 — the store has no public attempt setter
        task.id, lambda t: t.with_updates(attempts=t.max_attempts)
    )

    reactivate_resumable_work(store, lock=_HeldLock(), cooldown_minutes=0)

    after = store.get(task.id)
    assert after.status != "pending", (
        f"a checkpoint at {after.attempts}/{after.max_attempts} attempts was "
        "made runnable again"
    )


def test_a_pause_that_waits_on_a_human_is_not_reactivated(workspace: Path) -> None:
    """Not every pause is a budget pause. A row parked for a reason a clock
    cannot resolve must keep the human-only exit, or this repair would quietly
    widen into the territory `blocked` already owns."""
    store = _store(workspace)
    task = _park(store, stop_reason="awaiting_approval", trace_id="tr-h")

    reactivate_resumable_work(store, lock=_HeldLock(), cooldown_minutes=0)

    assert store.get(task.id).status == "paused"


def test_reparking_the_same_work_does_not_multiply_checkpoints(
    workspace: Path,
) -> None:
    """The growth trap. Reactivation is only safe if a resume that pauses again
    carries the same row forward; otherwise every failed retry leaves one more
    permanent row and the store grows without bound."""
    store = _store(workspace)
    first = _park(store, trace_id="tr-1")

    store.add_paused_checkpoint(
        goal="Answer the question: x",
        report={"stop_reason": "budget_exhausted", "trace_id": "tr-2",
                "current_phase": "planning"},
        resumed_from="tr-1",
    )

    paused = store.list(status="paused")
    assert len(paused) == 1, (
        f"a re-park created {len(paused)} rows for one piece of work; "
        "reactivation would turn this into unbounded growth"
    )
    assert paused[0].id == first.id, "the original row must carry forward"


def test_the_checkpoint_records_which_gateway_produced_it(workspace: Path) -> None:
    """`checkpoint_is_resumable_work` decides by gateway path at park time and
    the report keeps no trace of it — measured on all 14 live rows. A resumer
    that cannot ask who produced the work cannot decide whether resuming it
    unattended is right."""
    store = _store(workspace)
    task = store.add_paused_checkpoint(
        goal="Answer the question: x",
        report={"stop_reason": "budget_exhausted", "trace_id": "tr-g"},
        gateway_path="runtime",
    )
    assert (store.get(task.id).last_report or {}).get("gateway_path") == "runtime"


def test_a_fresh_pause_waits_out_the_cooldown(workspace: Path) -> None:
    """The single attempt these rows carry must not be spent while the window
    is still dry. Returning one immediately would burn it on a run that cannot
    finish, and the row would then be terminal for the wrong reason."""
    store = _store(workspace)
    task = _park(store)

    reactivate_resumable_work(store, lock=_HeldLock())  # default cooldown

    assert store.get(task.id).status == "paused"
    assert store.pending() == []


def test_the_lock_contract_is_enforced(workspace: Path) -> None:
    """Same reason as `recover_orphaned_tasks`: returning a row to the queue
    while a consumer may hold it in flight runs one piece of work twice."""
    store = _store(workspace)
    _park(store)

    class _FreeLock:
        held = False

    with pytest.raises(RuntimeError, match="single-instance lock"):
        reactivate_resumable_work(store, lock=_FreeLock(), cooldown_minutes=0)


def test_the_tick_actually_calls_it(workspace: Path) -> None:
    """A repair that is not on a live path is not a repair (MIR-131: thirteen
    maintenance actions exist and only a typed command can reach them). This
    one must be reachable by the unattended tick, so the call site is pinned."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("agent_tick.py").read_text(encoding="utf-8"))
    called = {
        getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "reactivate_resumable_work" in called, (
        "the reactivation pass exists but the tick never calls it — the exact "
        "shape MIR-131 measured thirteen times over"
    )


def test_the_queue_consumer_can_actually_run_what_reactivation_hands_it() -> None:
    """The layer under the layer, and the reason this repair did not stop at
    making a row runnable.

    `agent_tick` turns every claimed task into a config with
    `_config_from_task`, which refused any kind but `auto_run` — so a
    `resume_checkpoint` returned to the queue would be claimed (spending its
    single attempt), raise `ValueError`, and be buried by the exception handler.
    Reactivation without this would have converted fourteen silently stranded
    rows into fourteen automatically killed ones: worse than the defect.

    What the automatic path does with it is deliberately a RE-RUN, not a
    state-exact resume. Exact resumption is the human path (`--resume`, whose
    hint the interactive gateway prints); every automatic retry in this system
    re-runs with backoff, and the checkpoint's saved phase stays in
    `last_report` for whoever wants it.
    """
    from core.autonomous_runtime import _config_from_task
    from core.task_queue import RuntimeTask

    task = RuntimeTask(
        kind="resume_checkpoint",
        goal="Answer the question: what changed in core/loop.py",
        status="pending",
        dry_run=True,
        limit=1,
        learning_limit=1,
    )
    config = _config_from_task(task)
    assert config.goal == task.goal, (
        "the consumer cannot execute the kind reactivation produces, so the "
        "row is claimed, its one attempt spent, and then it dies on ValueError"
    )
    assert config.dry_run is True, (
        "a re-queued checkpoint must keep the conservative posture it was "
        "parked with"
    )
