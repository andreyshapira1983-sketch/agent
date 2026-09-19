"""TD-018 + cargo 3: the convergence sensor switches the next cycle to investigation.

Authorship: the agent (witness v1 structure, report builders, three-test plan and
docstring); courier transit — the goal-trace wrapper re-aimed at
``AutonomousRuntime.run`` (the neighbour harness's own device from
tests/test_work_session_convergence.py; the agent's v1 wrapped
``run_work_session``, which is called once and never sees per-cycle goals) and
the assertion arithmetic recomputed for this fake's dynamics (identical
signature from cycle 1) instead of being copied from the neighbour.

Computed dynamics with investigation_window=2, convergence_window=3 and an
identical report every cycle:
  c1: repeat=1   c2: repeat=2   c3: probe fires (goal for c4 = investigation)
  c4: probe cycle — excluded from the series (half-open probe, Circuit Breaker)
  c5: repeat=3 -> stop-crane, stop_reason="converged"
Goals as received by the runtime: [test, test, test, Investigate..., test].
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core import work_session as ws_mod
from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from core.work_session import WorkSessionConfig, run_work_session
from tests.conftest import FakeLLM
from tools.base import ToolRegistry

INVESTIGATION_PREFIX = "Investigate why goal"


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


def _identical_report():
    """A report equal to itself on every cycle (agent's builder, v1)."""
    return SimpleNamespace(
        status="completed",
        tasks=[
            SimpleNamespace(
                task=SimpleNamespace(kind="goal"),
                status="done",
                summary="same outcome",
            )
        ],
        stop_reason="",
    )


def _run_traced(config, agent, workspace, monkeypatch):
    """Record every goal AutonomousRuntime.run receives; return one fixed report.

    The per-cycle goal lives in the runtime config — wrapping the session entry
    (as the agent's v1 did) records only the outer goal, once.
    """
    goals: list[str] = []
    report = _identical_report()

    def traced_run(self, cfg):
        goals.append(cfg.goal)
        return report

    monkeypatch.setattr(ws_mod.AutonomousRuntime, "run", traced_run)
    result = run_work_session(config, agent=agent, workspace=workspace)
    return result, goals


def _config(**kw):
    base = {"goal": "test", "dry_run": True, "minutes": 60.0, "max_cycles": 8}
    base.update(kw)
    return WorkSessionConfig(**base)


class TestInvestigationProbe:
    def test_investigation_fires_before_the_stop_crane(
        self, workspace: Path, monkeypatch
    ):
        config = _config(
            investigate_on_repeat=True,
            investigation_window=2,
            convergence_window=3,
        )
        result, goals = _run_traced(config, _agent(workspace), workspace, monkeypatch)

        investigation_goals = [g for g in goals if g.startswith(INVESTIGATION_PREFIX)]
        assert len(investigation_goals) == 1, goals
        # Computed: probe goal is handed to cycle 4 (index 3) — after two
        # identical cycles counted at c2 and the firing pass at c3.
        assert goals.index(investigation_goals[0]) == 3, goals
        # The session must NOT have stopped at the investigation threshold.
        assert result.cycles_run > 3

    def test_the_stop_crane_survives_the_probe(
        self, workspace: Path, monkeypatch
    ):
        config = _config(
            investigate_on_repeat=True,
            investigation_window=2,
            convergence_window=3,
        )
        result, goals = _run_traced(config, _agent(workspace), workspace, monkeypatch)

        # Computed: probe at c4 is excluded from the series; the original
        # goal's third identical outcome lands at c5 and trips the crane.
        assert result.stop_reason == "converged"
        assert result.status == "completed"
        assert result.cycles_run == 5, goals
        # After the probe the original goal returned (half-open restore).
        assert goals[-1] == "test"

    def test_default_behaviour_unchanged(self, workspace: Path, monkeypatch):
        config = _config(convergence_window=3)  # investigate_on_repeat=False
        result, goals = _run_traced(config, _agent(workspace), workspace, monkeypatch)

        # Computed for THIS fake (identical from c1): repeat hits 3 at c3.
        assert result.stop_reason == "converged"
        assert result.cycles_run == 3, goals
        assert not [g for g in goals if g.startswith(INVESTIGATION_PREFIX)]
