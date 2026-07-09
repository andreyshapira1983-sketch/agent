"""TD-018: work-session convergence stop.

A long work session used to keep re-running the same goal for every one of its
``max_cycles`` even when successive cycles produced an identical result — the
agent re-asking the same question and making no forward progress, burning the
whole budget. The session now detects that steady state (an identical outcome
signature ``convergence_window`` times in a row, while cycles remain) and stops
early with ``stop_reason="converged"``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    _cycle_signature,
    run_work_session,
)
from tests.conftest import FakeLLM
from tools.base import ToolRegistry


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


def _report(status: str, tasks: list[tuple[str, str, str]]):
    """Build a minimal run-report-shaped object for the signature helper."""
    return SimpleNamespace(
        status=status,
        tasks=[
            SimpleNamespace(task=SimpleNamespace(kind=k), status=s, summary=sm)
            for (k, s, sm) in tasks
        ],
    )


# ── _cycle_signature ──────────────────────────────────────────────────────────

class TestCycleSignature:
    def test_identical_reports_share_signature(self):
        a = _report("completed", [("status", "done", "healthy"), ("goal", "done", "x")])
        b = _report("completed", [("status", "done", "healthy"), ("goal", "done", "x")])
        assert _cycle_signature(a) == _cycle_signature(b)

    def test_changed_summary_breaks_signature(self):
        a = _report("completed", [("goal", "done", "fixed lint in a.py")])
        b = _report("completed", [("goal", "done", "fixed lint in b.py")])
        assert _cycle_signature(a) != _cycle_signature(b)

    def test_changed_status_breaks_signature(self):
        a = _report("completed", [("goal", "done", "x")])
        b = _report("stopped", [("goal", "done", "x")])
        assert _cycle_signature(a) != _cycle_signature(b)

    def test_malformed_report_does_not_raise(self):
        # A report of an unexpected shape must never raise — it collapses to a
        # stable tuple so convergence tracking keeps working.
        sig = _cycle_signature(object())
        assert isinstance(sig, tuple)
        # Stable across calls, so a run of malformed reports still converges.
        assert sig == _cycle_signature(object())


# ── config validation ─────────────────────────────────────────────────────────

class TestConvergenceConfig:
    def test_defaults_enabled(self):
        cfg = WorkSessionConfig()
        assert cfg.stop_on_convergence is True
        assert cfg.convergence_window == 3

    def test_window_too_small_raises(self):
        with pytest.raises(ValueError, match="convergence_window"):
            WorkSessionConfig(convergence_window=1)


# ── run_work_session integration ──────────────────────────────────────────────

class TestConvergenceStop:
    def test_converges_before_max_cycles(self, workspace: Path):
        agent = _agent(workspace)
        # Identical dry-run cycles; window=3 should stop at cycle 3 of 8.
        config = WorkSessionConfig(
            goal="test", max_cycles=8, minutes=60.0, dry_run=True
        )
        result = run_work_session(config, agent=agent, workspace=workspace)

        assert result.stop_reason == "converged"
        assert result.status == "completed"
        assert result.cycles_run == config.convergence_window
        assert result.cycles_run < config.max_cycles

    def test_disabled_runs_all_cycles(self, workspace: Path):
        agent = _agent(workspace)
        config = WorkSessionConfig(
            goal="test",
            max_cycles=5,
            minutes=60.0,
            dry_run=True,
            stop_on_convergence=False,
        )
        result = run_work_session(config, agent=agent, workspace=workspace)

        assert result.cycles_run == 5
        assert result.stop_reason == ""
        assert result.status == "completed"

    def test_short_run_not_cut_short(self, workspace: Path):
        # max_cycles == convergence_window: the streak is only reached on the
        # final cycle, so the guard keeps the natural completion semantics.
        agent = _agent(workspace)
        config = WorkSessionConfig(
            goal="test", max_cycles=3, minutes=60.0, dry_run=True
        )
        result = run_work_session(config, agent=agent, workspace=workspace)

        assert result.cycles_run == 3
        assert result.stop_reason == ""
        assert result.status == "completed"

    def test_converged_event_logged(self, workspace: Path):
        import json

        agent = _agent(workspace)
        config = WorkSessionConfig(
            goal="test", max_cycles=8, minutes=60.0, dry_run=True
        )
        result = run_work_session(config, agent=agent, workspace=workspace)
        assert result.stop_reason == "converged"

        log_files = list((workspace / "logs").glob("*.jsonl"))
        events = []
        for lf in log_files:
            with open(lf, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        assert any(e.get("event") == "work_session_converged" for e in events)
