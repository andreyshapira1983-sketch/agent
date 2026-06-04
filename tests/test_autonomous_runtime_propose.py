"""Tests for the `propose` task kind in the autonomous runtime.

Proposals must be written to the approval inbox WITHOUT executing any.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.budget_governor import BudgetLimits
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry


def _agent(workspace: Path, llm: FakeLLM) -> AgentLoop:
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


class ProposeOnlyRuntime(AutonomousRuntime):
    """Runtime variant whose queue is exactly one `propose` task."""

    def _build_queue(self, config: AutonomousRuntimeConfig) -> list[AutonomousTask]:
        return [AutonomousTask("propose", "test-only propose task")]


def _config(**kw: Any) -> AutonomousRuntimeConfig:
    defaults: dict[str, Any] = dict(
        limit=1,
        include_tests=False,
        enable_reflection=False,
        include_proposals=True,
    )
    defaults.update(kw)
    return AutonomousRuntimeConfig(**defaults)


def test_propose_writes_three_inbox_items(workspace: Path) -> None:
    payload = {
        "proposals": [
            {"kind": "learn", "description": "Add tutorial source", "rationale": "broaden coverage", "est_cost": "low"},
            {"kind": "tests", "description": "Add tests for X", "rationale": "uncovered branch", "est_cost": "medium"},
            {"kind": "goal", "description": "Refactor module Y", "rationale": "tech debt", "est_cost": "high"},
        ]
    }
    llm = FakeLLM(responses=[json.dumps(payload)])
    agent = _agent(workspace, llm)
    inbox = ApprovalInbox()

    report = ProposeOnlyRuntime(agent, workspace=workspace, approval_inbox=inbox).run(_config())

    assert report.status == "completed"
    assert report.tasks[0].task.kind == "propose"
    assert report.tasks[0].status == "done"
    assert "proposals_written=3" in report.tasks[0].summary
    proposed = [item for item in inbox.items if item.operation == "proposed_task"]
    assert len(proposed) == 3
    for item in proposed:
        assert item.status == "pending"
        assert item.risk == "reversible"
        assert "kind" in item.payload and "description" in item.payload


def test_propose_handles_malformed_json(workspace: Path) -> None:
    llm = FakeLLM(responses=["not json at all"])
    agent = _agent(workspace, llm)
    inbox = ApprovalInbox()

    report = ProposeOnlyRuntime(agent, workspace=workspace, approval_inbox=inbox).run(_config())

    assert report.tasks[0].status == "failed"
    assert "malformed" in report.tasks[0].summary
    assert [item for item in inbox.items if item.operation == "proposed_task"] == []


def test_propose_dedups_against_existing_inbox(workspace: Path) -> None:
    inbox = ApprovalInbox()
    inbox.add(
        operation="proposed_task",
        summary="Add tutorial source",
        risk="reversible",
        payload={"description": "Add tutorial source"},
    )
    payload = {
        "proposals": [
            {"kind": "learn", "description": "Add tutorial source", "rationale": "dup", "est_cost": "low"},
            {"kind": "tests", "description": "New unique idea", "rationale": "fresh", "est_cost": "low"},
        ]
    }
    llm = FakeLLM(responses=[json.dumps(payload)])
    agent = _agent(workspace, llm)

    report = ProposeOnlyRuntime(agent, workspace=workspace, approval_inbox=inbox).run(_config())

    assert "proposals_written=1" in report.tasks[0].summary
    assert "dupes=1" in report.tasks[0].summary
    proposed = [item for item in inbox.items if item.operation == "proposed_task"]
    assert len(proposed) == 2  # one pre-existing + one new


def test_propose_respects_budget_cap(workspace: Path) -> None:
    payload = {
        "proposals": [
            {"kind": "learn", "description": "P1", "rationale": "r1", "est_cost": "low"},
            {"kind": "tests", "description": "P2", "rationale": "r2", "est_cost": "low"},
            {"kind": "goal", "description": "P3", "rationale": "r3", "est_cost": "low"},
        ]
    }
    llm = FakeLLM(responses=[json.dumps(payload)])
    agent = _agent(workspace, llm)
    inbox = ApprovalInbox()

    report = ProposeOnlyRuntime(agent, workspace=workspace, approval_inbox=inbox).run(
        _config(budgets=BudgetLimits(max_proposals_per_run=1))
    )

    assert "proposals_written=1" in report.tasks[0].summary
    proposed = [item for item in inbox.items if item.operation == "proposed_task"]
    assert len(proposed) == 1


def test_propose_strips_code_fences(workspace: Path) -> None:
    fenced = "```json\n" + json.dumps(
        {"proposals": [{"kind": "tests", "description": "Fenced one", "rationale": "ok", "est_cost": "low"}]}
    ) + "\n```"
    llm = FakeLLM(responses=[fenced])
    agent = _agent(workspace, llm)
    inbox = ApprovalInbox()

    report = ProposeOnlyRuntime(agent, workspace=workspace, approval_inbox=inbox).run(_config())

    assert "proposals_written=1" in report.tasks[0].summary


def test_propose_default_off_in_queue(workspace: Path) -> None:
    """When include_proposals is False (default), no propose task is queued."""
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    llm = FakeLLM(responses=[])
    agent = _agent(workspace, llm)

    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(limit=3, include_tests=False, enable_reflection=False)
    )

    kinds = [t.task.kind for t in report.tasks]
    assert "propose" not in kinds


def test_propose_appended_when_flag_on(workspace: Path) -> None:
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    payload = {
        "proposals": [
            {"kind": "tests", "description": "queued only", "rationale": "ok", "est_cost": "low"},
        ]
    }
    llm = FakeLLM(responses=[json.dumps(payload)])
    agent = _agent(workspace, llm)
    inbox = ApprovalInbox()

    report = AutonomousRuntime(agent, workspace=workspace, approval_inbox=inbox).run(
        AutonomousRuntimeConfig(
            limit=5,
            include_tests=False,
            enable_reflection=False,
            include_proposals=True,
        )
    )

    kinds = [t.task.kind for t in report.tasks]
    assert kinds[-1] == "propose"
