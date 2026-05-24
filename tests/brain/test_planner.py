"""
tests/brain/test_planner.py
"""
import pytest
from brain.planner import Planner, Plan, Step, StepStatus, PlanStatus


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def simple_steps() -> list[dict]:
    return [
        {"description": "Search web", "action": "search", "params": {"query": "Python"}},
        {"description": "Summarize results", "action": "summarize"},
        {"description": "Write report", "action": "write_file"},
    ]


def steps_with_deps() -> list[dict]:
    return [
        {"description": "Step A", "action": "fetch"},                     # id=0
        {"description": "Step B", "action": "parse", "depends_on": [0]},  # id=1, needs A
        {"description": "Step C", "action": "save",  "depends_on": [1]},  # id=2, needs B
    ]


# ------------------------------------------------------------------
# Plan creation
# ------------------------------------------------------------------

def test_create_plan_basic():
    p = Planner()
    plan = p.create_plan("Write a report", simple_steps())
    assert isinstance(plan, Plan)
    assert plan.goal == "Write a report"
    assert len(plan.steps) == 3
    assert plan.status == PlanStatus.ACTIVE


def test_all_steps_start_as_pending():
    p = Planner()
    p.create_plan("Goal", simple_steps())
    for step in p._active_plan.steps:
        assert step.status == StepStatus.PENDING


def test_steps_assigned_sequential_ids():
    p = Planner()
    p.create_plan("Goal", simple_steps())
    ids = [s.id for s in p._active_plan.steps]
    assert ids == [0, 1, 2]


# ------------------------------------------------------------------
# next_step — no dependencies
# ------------------------------------------------------------------

def test_next_step_returns_first_pending():
    p = Planner()
    p.create_plan("Goal", simple_steps())
    step = p.next_step()
    assert step is not None
    assert step.id == 0
    assert step.status == StepStatus.RUNNING


def test_next_step_none_when_no_plan():
    p = Planner()
    assert p.next_step() is None


def test_full_linear_execution():
    p = Planner()
    p.create_plan("Goal", simple_steps())

    for expected_id in range(3):
        step = p.next_step()
        assert step.id == expected_id
        p.mark_done(step.id, result=f"result_{expected_id}")

    # After all done — no active plan
    assert not p.has_active_plan()
    assert p.next_step() is None


# ------------------------------------------------------------------
# Dependencies
# ------------------------------------------------------------------

def test_dependency_blocks_next_step():
    p = Planner()
    p.create_plan("Goal", steps_with_deps())

    # Only step 0 is ready (no deps)
    step = p.next_step()
    assert step.id == 0

    # Step 1 and 2 not ready yet — can't get them
    # (step 0 is RUNNING, not DONE yet)
    step2 = p.next_step()
    assert step2 is None


def test_dependency_unlocks_after_done():
    p = Planner()
    p.create_plan("Goal", steps_with_deps())

    s0 = p.next_step()
    p.mark_done(s0.id)

    s1 = p.next_step()
    assert s1 is not None
    assert s1.id == 1


def test_chain_of_dependencies():
    p = Planner()
    p.create_plan("Goal", steps_with_deps())

    s0 = p.next_step()
    p.mark_done(s0.id)
    s1 = p.next_step()
    p.mark_done(s1.id)
    s2 = p.next_step()
    assert s2.id == 2
    p.mark_done(s2.id)

    assert not p.has_active_plan()


# ------------------------------------------------------------------
# Failure handling
# ------------------------------------------------------------------

def test_failed_step_blocks_dependent():
    p = Planner()
    p.create_plan("Goal", steps_with_deps())

    s0 = p.next_step()
    p.mark_failed(s0.id, error="Network error")

    # Plan should fail because step 1 depends on failed step 0
    assert not p.has_active_plan()
    assert p._history[-1].status == PlanStatus.FAILED


def test_failed_step_skip_dependents():
    p = Planner()
    p.create_plan("Goal", steps_with_deps())

    s0 = p.next_step()
    p.mark_failed(s0.id, error="Soft error", skip_dependents=True)

    # Dependents skipped → plan completes
    assert not p.has_active_plan()
    history = p._history[-1]
    assert history.status == PlanStatus.COMPLETE


def test_independent_step_survives_failure():
    """A step without dependency on the failed one should still run."""
    steps = [
        {"description": "Step A", "action": "fetch"},   # id=0, independent
        {"description": "Step B", "action": "parse"},   # id=1, independent
        {"description": "Step C", "action": "save", "depends_on": [0]},  # id=2, needs A
    ]
    p = Planner()
    p.create_plan("Goal", steps)

    s0 = p.next_step()
    assert s0.id == 0
    p.mark_failed(s0.id, error="fail", skip_dependents=True)

    # Step 1 is independent — should still be available
    s1 = p.next_step()
    assert s1 is not None
    assert s1.id == 1


# ------------------------------------------------------------------
# Plan completion
# ------------------------------------------------------------------

def test_plan_completes_after_all_done():
    p = Planner()
    p.create_plan("Goal", simple_steps())
    while p.has_active_plan():
        step = p.next_step()
        if step:
            p.mark_done(step.id)

    assert len(p._history) == 1
    assert p._history[0].status == PlanStatus.COMPLETE


def test_abort_plan():
    p = Planner()
    p.create_plan("Goal", simple_steps())
    p.abort()
    assert not p.has_active_plan()
    assert p._history[-1].status == PlanStatus.ABORTED


def test_new_plan_archives_old():
    p = Planner()
    p.create_plan("Goal 1", simple_steps())
    p.create_plan("Goal 2", simple_steps())
    assert p._active_plan.goal == "Goal 2"
    assert len(p._history) == 1
    assert p._history[0].goal == "Goal 1"
    assert p._history[0].status == PlanStatus.ABORTED


# ------------------------------------------------------------------
# Progress & status
# ------------------------------------------------------------------

def test_progress_tracking():
    p = Planner()
    p.create_plan("Goal", simple_steps())

    prog = p.progress()
    assert prog["total"] == 3
    assert prog["done"] == 0
    assert prog["percent"] == 0

    s = p.next_step()
    p.mark_done(s.id)
    prog = p.progress()
    assert prog["done"] == 1
    assert prog["percent"] == 33


def test_status_no_plan():
    p = Planner()
    status = p.status()
    assert status["active_plan"] is None


def test_status_with_plan():
    p = Planner()
    p.create_plan("My goal", simple_steps())
    status = p.status()
    assert status["active_plan"]["goal"] == "My goal"


# ------------------------------------------------------------------
# Step internals
# ------------------------------------------------------------------

def test_step_is_ready_no_deps():
    s = Step(id=0, description="x", action="x")
    assert s.is_ready(done_ids=set())


def test_step_is_ready_with_satisfied_deps():
    s = Step(id=1, description="x", action="x", depends_on=[0])
    assert s.is_ready(done_ids={0})


def test_step_not_ready_unsatisfied_deps():
    s = Step(id=1, description="x", action="x", depends_on=[0])
    assert not s.is_ready(done_ids=set())


def test_step_to_dict():
    s = Step(id=0, description="Search", action="search", params={"q": "test"})
    d = s.to_dict()
    assert d["id"] == 0
    assert d["action"] == "search"
    assert d["status"] == "pending"
