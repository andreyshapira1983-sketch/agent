"""Tests for brain/budget.py — BudgetController."""

from __future__ import annotations

import threading
import time

import pytest

from brain.budget import (
    BudgetController,
    BudgetExceeded,
    BudgetLimits,
)


# ════════════════════════════════════════════════════════════════════
# check + record
# ════════════════════════════════════════════════════════════════════

class TestCheckRecord:

    def test_check_passes_with_no_limits(self):
        ctrl = BudgetController()
        ok, _ = ctrl.check(tokens=10_000, dollars=5.0, seconds=300)
        assert ok

    def test_check_blocks_over_session_token_limit(self):
        ctrl = BudgetController(per_session=BudgetLimits(tokens=1000))
        ctrl.record(tokens=900)
        ok, reason = ctrl.check(tokens=200)
        assert not ok
        assert "session" in reason
        assert "tokens" in reason

    def test_record_accumulates(self):
        ctrl = BudgetController()
        ctrl.record(tokens=100, dollars=0.01)
        ctrl.record(tokens=200, dollars=0.02)
        usage = ctrl.session_usage()
        assert usage.tokens == 300
        assert usage.dollars == pytest.approx(0.03)

    def test_job_scope_charges_job_only_in_scope(self):
        ctrl = BudgetController(per_job=BudgetLimits(tokens=500))
        with ctrl.scope_job("j1"):
            ctrl.record(tokens=300)
            j1 = ctrl.job_usage("j1")
            assert j1 is not None and j1.tokens == 300
            ok, _ = ctrl.check(tokens=300)
            assert not ok  # would breach j1's 500-token limit
        # Outside scope, j1's budget no longer enforced
        ok, _ = ctrl.check(tokens=300)
        assert ok

    def test_multiple_jobs_track_separately(self):
        ctrl = BudgetController(per_job=BudgetLimits(tokens=100))
        with ctrl.scope_job("j1"):
            ctrl.record(tokens=80)
        with ctrl.scope_job("j2"):
            ok, _ = ctrl.check(tokens=80)
            assert ok  # j2 has its own budget
        assert ctrl.job_usage("j1").tokens == 80
        assert ctrl.job_usage("j2").tokens == 0


# ════════════════════════════════════════════════════════════════════
# charge() atomicity
# ════════════════════════════════════════════════════════════════════

class TestCharge:

    def test_charge_records_when_within_budget(self):
        ctrl = BudgetController(per_session=BudgetLimits(dollars=1.0))
        ctrl.charge(dollars=0.1)
        ctrl.charge(dollars=0.2)
        assert ctrl.session_usage().dollars == pytest.approx(0.3)

    def test_charge_raises_when_exceeding(self):
        ctrl = BudgetController(per_session=BudgetLimits(dollars=0.5))
        ctrl.charge(dollars=0.4)
        with pytest.raises(BudgetExceeded):
            ctrl.charge(dollars=0.2)
        # Failed charge must NOT be recorded
        assert ctrl.session_usage().dollars == pytest.approx(0.4)


# ════════════════════════════════════════════════════════════════════
# exhausted + state
# ════════════════════════════════════════════════════════════════════

class TestState:

    def test_is_exhausted_reflects_zero_budget_check(self):
        ctrl = BudgetController(per_session=BudgetLimits(tokens=100))
        assert not ctrl.is_exhausted()
        ctrl.record(tokens=100)
        # exactly at limit — is_exhausted() with cost 0 still passes
        # because check(0,0,0) = used + 0 > limit?  100+0 > 100 → False
        # so is_exhausted should be False.
        assert not ctrl.is_exhausted()
        ctrl.record(tokens=1)
        assert ctrl.is_exhausted()

    def test_state_snapshot(self):
        ctrl = BudgetController(per_job=BudgetLimits(tokens=200))
        with ctrl.scope_job("j1"):
            ctrl.record(tokens=50)
            state = ctrl.state()
            assert state["current_job"] == "j1"
            assert state["current_job_usage"]["tokens"] == 50
            assert state["limits"]["per_job"]["tokens"] == 200


# ════════════════════════════════════════════════════════════════════
# Thread safety smoke
# ════════════════════════════════════════════════════════════════════

class TestThreadSafety:

    def test_concurrent_records_correct_total(self):
        ctrl = BudgetController()
        N = 100
        per = 10
        threads = []

        def worker():
            for _ in range(per):
                ctrl.record(tokens=1)

        for _ in range(N):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert ctrl.session_usage().tokens == N * per
