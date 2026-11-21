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


# ── config validation ─────────────────────────────────────────────────────────

class TestConvergenceConfig:
    def test_window_too_small_raises(self):
        with pytest.raises(ValueError, match="convergence_window"):
            WorkSessionConfig(convergence_window=1)


# ── run_work_session integration ──────────────────────────────────────────────

class TestConvergenceStop:
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
