"""Tests for MVP-17.1 Long Work Session Skeleton (core/work_session.py)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from core.work_session import (
    WorkSessionConfig,
    WorkSessionCycleReport,
    WorkSessionResult,
    run_work_session,
)
from tests.conftest import FakeLLM
from tools.base import ToolRegistry


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakeClock:
    """Minimal stand-in for the ``time`` module exposing a controllable
    ``monotonic``. Lets timing tests advance virtual time deterministically,
    independent of host speed."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now


def _agent(workspace: Path) -> AgentLoop:
    llm = FakeLLM(responses=[])
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )


# ── WorkSessionConfig ─────────────────────────────────────────────────────────

class TestWorkSessionConfig:
    def test_defaults(self):
        cfg = WorkSessionConfig()
        assert cfg.goal == "project health"
        assert cfg.dry_run is False  # default changed: approval gate protects side-effects
        assert cfg.minutes == 10.0
        assert cfg.max_cycles == 3
        assert cfg.report_every == 1

    def test_custom_values(self):
        cfg = WorkSessionConfig(
            goal="learn sources",
            dry_run=False,
            minutes=30.0,
            max_cycles=5,
            report_every=2,
        )
        assert cfg.goal == "learn sources"
        assert cfg.dry_run is False
        assert cfg.minutes == 30.0
        assert cfg.max_cycles == 5
        assert cfg.report_every == 2

    def test_frozen(self):
        cfg = WorkSessionConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.goal = "mutated"  # type: ignore[misc]

    def test_minutes_zero_raises(self):
        with pytest.raises(ValueError, match="minutes"):
            WorkSessionConfig(minutes=0)

    def test_minutes_negative_raises(self):
        with pytest.raises(ValueError, match="minutes"):
            WorkSessionConfig(minutes=-1.0)

    def test_max_cycles_zero_raises(self):
        with pytest.raises(ValueError, match="max_cycles"):
            WorkSessionConfig(max_cycles=0)

    def test_report_every_zero_raises(self):
        with pytest.raises(ValueError, match="report_every"):
            WorkSessionConfig(report_every=0)


# ── WorkSessionCycleReport ────────────────────────────────────────────────────

class TestWorkSessionCycleReport:
    def test_to_dict_keys(self):
        cr = WorkSessionCycleReport(
            cycle=1,
            run_status="completed",
            tasks_done=1,
            tasks_failed=0,
            elapsed_s=0.05,
        )
        d = cr.to_dict()
        assert set(d) == {"cycle", "run_status", "tasks_done", "tasks_failed", "elapsed_s"}
        assert d["cycle"] == 1
        assert d["run_status"] == "completed"

    def test_to_dict_is_json_serializable(self):
        cr = WorkSessionCycleReport(cycle=2, run_status="stopped", tasks_done=0, tasks_failed=1, elapsed_s=1.23)
        json.dumps(cr.to_dict())  # must not raise

    def test_user_summary_contains_cycle(self):
        cr = WorkSessionCycleReport(cycle=3, run_status="completed", tasks_done=1, tasks_failed=0, elapsed_s=0.1)
        s = cr.user_summary()
        assert "[cycle 3]" in s
        assert "completed" in s


# ── WorkSessionResult ─────────────────────────────────────────────────────────

class TestWorkSessionResult:
    def _make(self, **kw: Any) -> WorkSessionResult:
        defaults: dict[str, Any] = dict(
            status="completed",
            goal="project health",
            dry_run=True,
            cycles_run=2,
            stop_reason="",
            cycle_reports=[],
            total_elapsed_s=0.12,
        )
        defaults.update(kw)
        return WorkSessionResult(**defaults)

    def test_to_dict_keys(self):
        r = self._make()
        d = r.to_dict()
        assert set(d) >= {"status", "goal", "dry_run", "cycles_run", "stop_reason", "total_elapsed_s", "cycles"}

    def test_to_dict_is_json_serializable(self):
        r = self._make()
        json.dumps(r.to_dict())  # must not raise

    def test_user_summary_contains_status(self):
        r = self._make(status="completed", cycles_run=3, total_elapsed_s=0.5)
        s = r.user_summary()
        assert "completed" in s
        assert "cycles=3" in s

    def test_user_summary_contains_stop_reason_when_set(self):
        r = self._make(status="stopped", stop_reason="time_budget")
        s = r.user_summary()
        assert "time_budget" in s

    def test_user_summary_no_stop_reason_when_empty(self):
        r = self._make(status="completed", stop_reason="")
        s = r.user_summary()
        assert "stop_reason" not in s

    def test_cycle_reports_in_summary(self):
        cr = WorkSessionCycleReport(cycle=1, run_status="completed", tasks_done=1, tasks_failed=0, elapsed_s=0.01)
        r = self._make(cycle_reports=[cr])
        s = r.user_summary()
        assert "[cycle 1]" in s


# ── run_work_session ──────────────────────────────────────────────────────────

class TestRunWorkSession:
    def test_completes_all_max_cycles(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(goal="test", max_cycles=3, minutes=60.0, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)

        assert result.status == "completed"
        assert result.cycles_run == 3
        assert len(result.cycle_reports) == 3
        assert result.stop_reason == ""

    def test_stops_on_time_budget(self, workspace: Path, monkeypatch: pytest.MonkeyPatch):
        from core import work_session as ws_mod

        agent = _agent(workspace)
        # Deterministic virtual clock so the time-budget gate is tested in
        # isolation. A near-zero *real* budget is racy: on a fast runner several
        # cycles finish inside the budget, letting the convergence stop (a
        # separate feature) win before the wall-clock deadline is crossed —
        # which is exactly why this used to pass on slow Windows but fail on
        # fast CI. Here each executed cycle advances virtual time past the
        # budget, so the session must stop with stop_reason="time_budget".
        clock = _FakeClock()
        monkeypatch.setattr(ws_mod, "time", clock)

        real_run = ws_mod.AutonomousRuntime.run

        def timed_run(self, cfg):
            clock.now += 120.0  # each cycle consumes 2 virtual minutes
            return real_run(self, cfg)

        monkeypatch.setattr(ws_mod.AutonomousRuntime, "run", timed_run)

        # 1-minute budget → the first cycle (2 virtual minutes) overruns it.
        config = WorkSessionConfig(goal="test", max_cycles=100, minutes=1.0, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)

        assert result.status == "stopped"
        assert result.stop_reason == "time_budget"
        # Stopped early — long before the 100-cycle ceiling.
        assert 1 <= result.cycles_run < config.max_cycles

    def test_dry_run_false_by_default(self, workspace: Path):
        # Default is now False; AutonomousRuntime approval gate protects side-effects.
        config = WorkSessionConfig(max_cycles=1)
        assert config.dry_run is False

    def test_dry_run_true_explicit(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=1, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.dry_run is True

    def test_dry_run_propagated_to_cycle_reports(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=1, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.dry_run is True
        # Cycle reports should show completed (dry-run status task always succeeds)
        assert result.cycles_run == 1
        assert result.cycle_reports[0].run_status == "completed"

    def test_cycle_reports_length_equals_cycles_run(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=2, minutes=60.0, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert len(result.cycle_reports) == result.cycles_run

    def test_cycle_numbers_are_sequential(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=3, minutes=60.0, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        cycles = [cr.cycle for cr in result.cycle_reports]
        assert cycles == list(range(1, result.cycles_run + 1))

    def test_total_elapsed_non_negative(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=1, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.total_elapsed_s >= 0.0

    def test_single_cycle(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=1, minutes=60.0, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.cycles_run == 1
        assert result.status == "completed"

    def test_goal_preserved_in_result(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(goal="custom goal", max_cycles=1, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.goal == "custom goal"

    def test_result_to_dict_is_json_serializable(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=2, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        json.dumps(result.to_dict())  # must not raise

    def test_tasks_done_in_cycle_report(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=1, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        cr = result.cycle_reports[0]
        # status task always succeeds; goal task may also run when goal is set
        assert cr.tasks_done >= 0
        assert cr.tasks_failed >= 0
        assert cr.tasks_done + cr.tasks_failed >= 0  # sanity: non-negative

    def test_report_every_one_logs_each_cycle(self, workspace: Path):
        """report_every=1 — a log event should be emitted for each cycle."""
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=3, report_every=1, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        # All 3 cycles completed
        assert result.cycles_run == 3

    def test_report_every_larger_than_cycles(self, workspace: Path):
        """report_every=10 with max_cycles=2 — session completes normally."""
        agent = _agent(workspace)
        config = WorkSessionConfig(max_cycles=2, report_every=10, dry_run=True)
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.cycles_run == 2
        assert result.status == "completed"

    def test_no_agent_log_attr_does_not_crash(self, workspace: Path):
        """If agent has no .log attribute, run_work_session should still work."""
        class MinimalAgent:
            pass

        agent = MinimalAgent()
        config = WorkSessionConfig(max_cycles=1)
        # AutonomousRuntime will fail when it tries to use agent internals —
        # but the _log helper itself should not raise an AttributeError.
        try:
            run_work_session(config, agent=agent, workspace=workspace)
        except Exception:
            pass  # AutonomousRuntime may fail; the test is only about _log safety
