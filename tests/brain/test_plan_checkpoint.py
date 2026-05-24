"""Tests for brain/planner.py — Plan serialisation + PlanCheckpointStore."""

from __future__ import annotations

import pytest

from brain.planner import (
    Plan,
    PlanCheckpointStore,
    PlanStatus,
    Planner,
    Step,
    StepStatus,
)


# ════════════════════════════════════════════════════════════════════
# Round-trip
# ════════════════════════════════════════════════════════════════════

class TestRoundTrip:

    def test_empty_plan_roundtrip(self):
        plan = Plan(goal="empty")
        copy = Plan.from_dict(plan.to_dict())
        assert copy.goal == "empty"
        assert copy.steps == []
        assert copy.status is PlanStatus.ACTIVE

    def test_plan_with_steps_roundtrip(self):
        planner = Planner()
        plan = planner.create_plan(goal="test goal", steps=[
            {"description": "step 1", "action": "fetch", "params": {"url": "x"}},
            {"description": "step 2", "action": "write", "depends_on": [0]},
        ])
        # Advance one step
        s = planner.next_step()
        planner.mark_done(s.id, result="ok")
        copy = Plan.from_dict(plan.to_dict())
        assert copy.goal == "test goal"
        assert len(copy.steps) == 2
        assert copy.steps[0].status is StepStatus.DONE
        assert copy.steps[1].status is StepStatus.PENDING

    def test_unknown_status_falls_back(self):
        # If a future Status enum value sneaks in, from_dict shouldn't crash
        # — we explicitly use a known value here as canary.
        data = {
            "goal": "x",
            "status": "active",
            "steps": [{"id": 0, "description": "d", "action": "a", "status": "pending"}],
        }
        plan = Plan.from_dict(data)
        assert plan.status is PlanStatus.ACTIVE


# ════════════════════════════════════════════════════════════════════
# PlanCheckpointStore
# ════════════════════════════════════════════════════════════════════

class TestCheckpointStore:

    def test_save_and_load(self, tmp_path):
        store = PlanCheckpointStore(tmp_path / "ck.db")
        plan = Planner().create_plan(goal="g", steps=[
            {"description": "x", "action": "a"},
        ])
        store.save("job1", plan)
        loaded = store.latest("job1")
        assert loaded is not None
        assert loaded.goal == "g"
        assert len(loaded.steps) == 1
        store.close()

    def test_save_history(self, tmp_path):
        store = PlanCheckpointStore(tmp_path / "ck.db")
        planner = Planner()
        plan = planner.create_plan(goal="g", steps=[
            {"description": "x", "action": "a"},
        ])
        store.save("job1", plan)
        s = planner.next_step()
        planner.mark_done(s.id)
        store.save("job1", plan)
        hist = store.history("job1")
        assert len(hist) == 2
        # The second snapshot has a DONE step
        assert hist[1].steps[0].status is StepStatus.DONE
        store.close()

    def test_unfinished_jobs(self, tmp_path):
        store = PlanCheckpointStore(tmp_path / "ck.db")
        planner = Planner()
        plan_a = planner.create_plan(goal="A", steps=[{"description": "x", "action": "a"}])
        store.save("a", plan_a)

        planner_b = Planner()
        plan_b = planner_b.create_plan(goal="B", steps=[{"description": "x", "action": "a"}])
        # Force a terminal status for B
        plan_b.status = PlanStatus.COMPLETE
        store.save("b", plan_b)

        unfinished = store.unfinished_jobs()
        assert "a" in unfinished
        assert "b" not in unfinished
        store.close()

    def test_clear(self, tmp_path):
        store = PlanCheckpointStore(tmp_path / "ck.db")
        plan = Planner().create_plan(goal="g", steps=[{"description": "x", "action": "a"}])
        store.save("j", plan)
        store.save("j", plan)
        assert len(store.history("j")) == 2
        n = store.clear("j")
        assert n == 2
        assert store.latest("j") is None
        store.close()
