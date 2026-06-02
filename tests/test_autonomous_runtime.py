from __future__ import annotations

from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.budget_governor import BudgetLimits
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from core.task_queue import TaskQueueStore
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry


class FakeRunTestsTool(Tool):
    name = "run_tests"
    description = "fake tests"
    risk = "reversible"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "timed_out": False,
            "passed": 3,
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
        }


class LLMCountingRuntime(AutonomousRuntime):
    def _task_status(self, task):
        # Make 2 LLM calls so that a budget limit of 1 is reliably exceeded.
        self.agent.llm.complete(system="budget", user="count-1")
        self.agent.llm.complete(system="budget", user="count-2")
        return super()._task_status(task)


def _agent(workspace: Path, *, with_tests: bool = True) -> AgentLoop:
    registry = ToolRegistry()
    if with_tests:
        registry.register(FakeRunTestsTool())
    llm = FakeLLM(responses=[])
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


def test_auto_runtime_dry_run_status_and_learning(workspace: Path):
    (workspace / "README.md").write_text(
        "The autonomous runtime runs bounded health checks.",
        encoding="utf-8",
    )
    agent = _agent(workspace, with_tests=False)

    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(limit=2, include_tests=False)
    )

    assert report.status == "completed"
    assert [task.task.kind for task in report.tasks] == ["status", "learn"]
    assert report.tasks[1].status == "done"
    assert report.budget["used"]["learning_runs"] == 1


def test_auto_runtime_can_run_test_health_check(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace)

    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(limit=3, include_tests=True)
    )

    assert report.status == "completed"
    assert report.tasks[-1].task.kind == "tests"
    assert report.tasks[-1].status == "done"
    assert "passed=3" in report.tasks[-1].summary


def test_auto_runtime_blocks_non_dry_run_into_approval_inbox(workspace: Path):
    agent = _agent(workspace, with_tests=False)

    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(
            goal="approved goal",
            dry_run=False,
            limit=2,
            include_tests=False,
            learning_limit=3,
        )
    )

    assert report.status == "blocked"
    assert report.approvals["pending"] == 1
    assert "approval required" in report.stop_reason
    item = report.approvals["items"][0]
    assert item["operation"] == "autonomous_runtime.allow_effects"
    assert item["payload"]["goal"] == "approved goal"
    assert item["payload"]["dry_run"] is False
    assert item["payload"]["limit"] == 2
    assert item["payload"]["include_tests"] is False
    assert item["payload"]["learning_limit"] == 3


def test_auto_runtime_runs_non_dry_run_after_explicit_effects_approval(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    inbox = ApprovalInbox()

    report = AutonomousRuntime(agent, workspace=workspace, approval_inbox=inbox).run(
        AutonomousRuntimeConfig(
            dry_run=False,
            effects_approved=True,
            limit=2,
            include_tests=False,
        )
    )

    assert report.status == "completed"
    assert report.dry_run is False
    assert report.approvals["pending"] == 0
    assert agent.source_registry_store.count()["sources"] >= 1


def test_auto_runtime_stops_on_budget_denial(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)

    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(
            limit=3,
            include_tests=False,
            budgets=BudgetLimits(max_cycles=1),
        )
    )

    assert report.status == "stopped"
    assert len(report.tasks) == 1
    assert report.budget["denials"]


def test_auto_runtime_runs_persistent_task_queue(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="project health", include_tests=False, limit=2)

    report = AutonomousRuntime(agent, workspace=workspace).run_task_queue(
        queue,
        max_tasks=1,
    )

    assert report.status == "completed"
    assert report.processed[0].task_id == task.id
    assert report.processed[0].status == "done"
    assert queue.get(task.id).status == "done"


def test_auto_runtime_empty_queue_reports_empty(workspace: Path):
    agent = _agent(workspace, with_tests=False)
    queue = TaskQueueStore(workspace / "tasks.jsonl")

    report = AutonomousRuntime(agent, workspace=workspace).run_task_queue(queue)

    assert report.status == "empty"
    assert report.processed == []


def test_auto_runtime_queue_uses_one_budget_across_tasks(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    first = queue.add(goal="first", include_tests=False, limit=2)
    second = queue.add(goal="second", include_tests=False, limit=2)

    report = AutonomousRuntime(agent, workspace=workspace).run_task_queue(
        queue,
        max_tasks=2,
        budgets=BudgetLimits(max_cycles=2),
    )

    assert report.status == "stopped"
    assert report.processed[0].task_id == first.id
    assert report.processed[0].status == "done"
    assert report.processed[1].task_id == second.id
    assert report.processed[1].status == "failed"
    assert queue.get(first.id).status == "done"
    assert queue.get(second.id).status == "failed"


def test_auto_runtime_enforces_llm_call_budget(workspace: Path):
    agent = _agent(workspace, with_tests=False)

    report = LLMCountingRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(
            limit=1,
            include_tests=False,
            budgets=BudgetLimits(max_cycles=1, max_llm_calls=1),
        )
    )

    assert report.status == "stopped"
    assert report.budget["denials"][0]["counter"] == "llm_calls"


def test_auto_runtime_queue_can_run_specific_task_ids(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    old = queue.add(goal="old", priority=0, include_tests=False, limit=1)
    new = queue.add(goal="new", priority=5, include_tests=False, limit=1)

    report = AutonomousRuntime(agent, workspace=workspace).run_task_queue(
        queue,
        max_tasks=1,
        task_ids=(new.id,),
    )

    assert report.status == "completed"
    assert report.processed[0].task_id == new.id
    assert queue.get(new.id).status == "done"
    assert queue.get(old.id).status == "pending"
