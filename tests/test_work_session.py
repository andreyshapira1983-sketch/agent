"""Tests for MVP-17.1 Long Work Session Skeleton (core/work_session.py)."""
from __future__ import annotations

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


# ── WorkSessionResult ─────────────────────────────────────────────────────────

class TestWorkSessionResult:
    def _make(self, **kw: Any) -> WorkSessionResult:
        defaults: dict[str, Any] = {
            "status": "completed",
            "goal": "project health",
            "dry_run": True,
            "cycles_run": 2,
            "stop_reason": "",
            "cycle_reports": [],
            "total_elapsed_s": 0.12,
        }
        defaults.update(kw)
        return WorkSessionResult(**defaults)


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
