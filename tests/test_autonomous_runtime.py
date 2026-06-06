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


class FakeTimedOutRunTestsTool(Tool):
    name = "run_tests"
    description = "fake tests that time out"
    risk = "reversible"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": None,
            "timed_out": True,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
        }


class FakeFailingRunTestsTool(Tool):
    name = "run_tests"
    description = "fake tests that fail at one concrete test file"
    risk = "reversible"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 1,
            "timed_out": False,
            "passed": 2,
            "failed": 1,
            "errors": 0,
            "failed_tests": ["tests/test_target_under_repair.py::test_broken"],
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


def test_timed_out_tests_are_reported_inconclusive_not_done(workspace: Path):
    # A run that times out (passed=0, failed=0, exit_code=None) must NOT be
    # reported as a clean "done" — that is the false-success bug in Layer A.
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(FakeTimedOutRunTestsTool())
    llm = FakeLLM(responses=[])
    agent = AgentLoop(
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

    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(limit=3, include_tests=True)
    )

    tests_report = report.tasks[-1]
    assert tests_report.task.kind == "tests"
    assert tests_report.status == "inconclusive"
    assert tests_report.status != "done"
    assert tests_report.details["timed_out"] is True
    assert tests_report.details["exit_code"] is None



def test_tick_marks_timed_out_run_inconclusive_not_healthy(workspace: Path, monkeypatch):
    """End-to-end daemon tick regression.

    Drive the real ``run_tick`` with a run_tests tool that times out
    (timed_out=true, exit_code=null, passed=0, failed_or_errors=0). The tick
    log must record the honest verdict — result_status/tests_health =
    "inconclusive" — and must NEVER mark this tick as done/pass/healthy, while
    keeping run_status=completed as a separate signal.
    """
    import json
    import agent_tick

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

    # Minimal agent whose only test tool reports a timed-out (unfinished) run.
    registry = ToolRegistry()
    registry.register(FakeTimedOutRunTestsTool())
    llm = FakeLLM(responses=[])
    agent = AgentLoop(
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

    # run_tick lazily does `from main import build_agent`; patch that symbol.
    import main
    monkeypatch.setattr(main, "build_agent", lambda *a, **k: agent)

    # run_tick does os.environ.setdefault("AGENT_TEST_TIMEOUT_SECONDS", ...);
    # pin it via monkeypatch so the global env mutation is reverted on teardown
    # and cannot leak into other tests (e.g. the default-timeout assertion).
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    # Enqueue a pending auto_run task that includes the tests health check.
    queue = TaskQueueStore(workspace / "data" / "task_queue.jsonl")
    queue.add(goal="project health", dry_run=True, include_tests=True, limit=3)

    exit_code = agent_tick.run_tick(workspace, dry_run=True)
    assert exit_code == 0

    # Parse the append-only tick log.
    log_path = workspace / "logs" / "daemon_tick.jsonl"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    by_event = {}
    for ev in events:
        by_event.setdefault(ev.get("event"), []).append(ev)

    # task_done carries the honest result_status, kept separate from run_status.
    task_done = by_event["task_done"][-1]
    assert task_done["result_status"] == "inconclusive"
    assert task_done["run_status"] == "completed"  # the run itself finished

    # tick_complete must report inconclusive, never a healthy/pass verdict.
    tick_complete = by_event["tick_complete"][-1]
    assert tick_complete["tests_health"] == "inconclusive"
    assert tick_complete["result_status"] == "inconclusive"
    assert tick_complete["tests_health"] not in {"pass", "done"}

    # An explicit inconclusive marker event must have been logged.
    assert "tests_inconclusive" in by_event

    # No event anywhere may claim the timed-out run passed.
    for ev in events:
        assert ev.get("tests_health") != "pass"
        assert ev.get("result_status") not in {"done", "pass"}


def test_tick_failed_tests_drive_a_repair_proposal_into_the_inbox(
    workspace: Path, monkeypatch
):
    """End-to-end daemon auto-repair regression — the seam that hid 3 bugs.

    The `run_tick -> task tests status == "failed" -> _maybe_propose_repair ->
    real ApprovalInbox.add` path was only ever exercised by hand. Here we drive
    the REAL `run_tick` with a run_tests tool that fails at exactly one existing
    test file. The proposal generator is stubbed to return a REAL
    ProposalGenerationReport carrying a REAL RepairProposal (so the field shapes
    `prop.path` / `prop.reason` / `prop.proposed_content` are bound to the real
    dataclass, not a guessed fake). The tick must:

      * log a `repair_attempt` event with repair_proposed=True and the target,
      * record the honest result_status=="failed" on the task,
      * land a `repair_proposal` item in the real on-disk inbox whose payload
        was built from the real proposal fields.
    """
    import json

    import agent_tick
    from core.repair_proposal import ProposalGenerationReport
    from core.self_repair import RepairProposal

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

    # The failing test must point at a single existing file for
    # `_repair_target_from_failures` to derive a concrete target.
    target_rel = "tests/test_target_under_repair.py"
    target_file = workspace / target_rel
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        "def test_broken():\n    assert 1 == 2\n", encoding="utf-8"
    )

    # Stub the generator (lazily imported inside _maybe_propose_repair as
    # `from core.repair_proposal import RepairProposalGenerator`) so it returns
    # a REAL proposed report with a REAL RepairProposal — deterministic, no LLM.
    class _FakeProposingGenerator:
        def __init__(self, *, llm, workspace_root, **_kw):
            assert llm is not None
            assert Path(workspace_root) == workspace

        def generate(self, *, target_path, **_kw):
            prop = RepairProposal(
                path=target_path,
                proposed_content="def test_broken():\n    assert 1 == 1\n",
                reason="correct the inverted assertion",
                confidence=0.9,
                evidence=(f"{target_path}::test_broken",),
            )
            return ProposalGenerationReport(
                status="proposed",
                proposal=prop,
                diagnosis="assertion compared 1 == 2",
                confidence=0.9,
                evidence=("baseline failed at test_broken", "single failing file"),
            )

    monkeypatch.setattr(
        "core.repair_proposal.RepairProposalGenerator", _FakeProposingGenerator
    )

    # Agent whose only test tool reports one concrete failing test file.
    registry = ToolRegistry()
    registry.register(FakeFailingRunTestsTool())
    llm = FakeLLM(responses=[])
    agent = AgentLoop(
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

    import main
    monkeypatch.setattr(main, "build_agent", lambda *a, **k: agent)
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    queue = TaskQueueStore(workspace / "data" / "task_queue.jsonl")
    queue.add(goal="project health", dry_run=True, include_tests=True, limit=3)

    exit_code = agent_tick.run_tick(workspace, dry_run=True)
    assert exit_code == 0

    log_path = workspace / "logs" / "daemon_tick.jsonl"
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    by_event: dict[str, list[dict]] = {}
    for ev in events:
        by_event.setdefault(ev.get("event"), []).append(ev)

    # 1. The failed-tests branch actually fired and proposed a repair.
    repair_attempt = by_event["repair_attempt"][-1]
    assert repair_attempt["repair_proposed"] is True
    assert repair_attempt["target"] == target_rel

    # 2. The task itself was recorded with the honest failed verdict.
    task_done = by_event["task_done"][-1]
    assert task_done["result_status"] == "failed"

    # 3. A real proposal landed in the real on-disk inbox, built from the real
    #    RepairProposal fields (path/reason/proposed_content) — the exact seam
    #    where the AttributeError bug used to live.
    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    proposals = [i for i in inbox.pending() if i.operation == "repair_proposal"]
    assert len(proposals) == 1
    payload = proposals[0].payload
    assert payload["target_file"] == target_rel
    assert payload["failed_count"] == 1
    assert payload["confidence"] == 0.9
    assert payload["failed_tests"] == [f"{target_rel}::test_broken"]
    assert "assert 1 == 1" in payload["proposed_content_preview"]


def test_tick_dry_run_streak_grows_then_resets_on_live(workspace: Path, monkeypatch):
    """End-to-end dry-run visibility regression.

    Only ticks that actually run a dry-run pass (a pending task was processed)
    grow dry_run_streak; an IDLE no-op tick in between carries it forward
    UNCHANGED so the stall signal is not diluted by mere script invocations.
    A following live tick resets the streak to 0 and never implies effects ran.
    """
    import json
    import agent_tick

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

    agent = _agent(workspace, with_tests=False)
    import main
    monkeypatch.setattr(main, "build_agent", lambda *a, **k: agent)
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")

    hb_path = workspace / "data" / "daemon_heartbeat.json"
    task_store = TaskQueueStore(workspace / agent_tick.TASK_QUEUE_PATH)

    def _enqueue_work() -> None:
        task_store.add(goal="health check", dry_run=True, include_tests=False)

    # Tick 1 (dry-run, did work): a pending task is processed -> streak 1.
    _enqueue_work()
    assert agent_tick.run_tick(workspace, dry_run=True) == 0
    hb1 = json.loads(hb_path.read_text(encoding="utf-8"))
    assert hb1["mode"] == "dry_run"
    assert hb1["effects"] == "disabled"
    assert hb1["processed_effects"] == 0
    assert hb1["tasks_processed"] >= 1
    assert hb1["dry_run_streak"] == 1

    # Tick 2 (dry-run, IDLE no-op): nothing pending -> streak carries forward.
    assert agent_tick.run_tick(workspace, dry_run=True) == 0
    hb_idle = json.loads(hb_path.read_text(encoding="utf-8"))
    assert hb_idle["tasks_processed"] == 0
    assert hb_idle["dry_run_streak"] == 1  # NOT inflated by an idle tick

    # Tick 3 (dry-run, did work): streak increments to 2.
    _enqueue_work()
    assert agent_tick.run_tick(workspace, dry_run=True) == 0
    hb2 = json.loads(hb_path.read_text(encoding="utf-8"))
    assert hb2["dry_run_streak"] == 2
    assert hb2["mode"] == "dry_run"

    # Tick 4 (live): streak resets, nothing implies effects were applied.
    assert agent_tick.run_tick(workspace, dry_run=False) == 0
    hb3 = json.loads(hb_path.read_text(encoding="utf-8"))
    assert hb3["mode"] == "live"
    assert hb3["dry_run_streak"] == 0
    assert hb3["processed_effects"] == 0

    # The tick log never claims an effect was applied.
    events = [
        json.loads(line)
        for line in (workspace / "logs" / "daemon_tick.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for ev in events:
        assert ev.get("processed_effects", 0) == 0


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


def test_auto_runtime_stop_opens_a_structured_incident(workspace: Path):
    """A circuit/budget stop must leave a durable incident trace, not just a log line.

    Mirrors the budget-denial stop but passes an IncidentLog collaborator. The
    halted run must open exactly one high-severity incident that forces a human
    look (B-2 wiring).
    """
    from core.incident import IncidentLog

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    incident_log = IncidentLog(path=workspace / "data" / "incidents.jsonl")

    report = AutonomousRuntime(
        agent, workspace=workspace, incident_log=incident_log
    ).run(
        AutonomousRuntimeConfig(
            limit=3,
            include_tests=False,
            budgets=BudgetLimits(max_cycles=1),
        )
    )

    assert report.status == "stopped"
    open_incidents = incident_log.open_incidents()
    assert len(open_incidents) == 1
    inc = open_incidents[0]
    assert inc.trigger == "autonomous_run_stopped"
    assert inc.affected_module == "core.autonomous_runtime"
    assert inc.severity == "high"
    assert inc.human_escalation is True   # high severity always demands a human
    assert inc.needs_human is True


def test_auto_runtime_stop_does_not_open_duplicate_incidents(workspace: Path):
    """A second stop while one incident is still open must not spam duplicates."""
    from core.incident import IncidentLog

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    incident_log = IncidentLog(path=workspace / "data" / "incidents.jsonl")
    runtime = AutonomousRuntime(
        agent, workspace=workspace, incident_log=incident_log
    )
    cfg = AutonomousRuntimeConfig(
        limit=3, include_tests=False, budgets=BudgetLimits(max_cycles=1)
    )

    runtime.run(cfg)
    runtime.run(cfg)

    assert len(incident_log.open_incidents()) == 1


def test_auto_runtime_completed_run_opens_no_incident(workspace: Path):
    """A clean completed run must NOT open any incident."""
    from core.incident import IncidentLog

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    incident_log = IncidentLog(path=workspace / "data" / "incidents.jsonl")

    report = AutonomousRuntime(
        agent, workspace=workspace, incident_log=incident_log
    ).run(AutonomousRuntimeConfig(limit=2, include_tests=False))

    assert report.status == "completed"
    assert incident_log.open_incidents() == []


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


def test_dry_run_goal_blocks_effect_tools_and_restores(workspace: Path):
    """A dry-run goal task must deny file_write/shell_exec so the agent
    cannot leave junk files (e.g. install_coverage.bat) behind, then
    restore the policy's block-list afterwards."""
    from core.autonomous_runtime import AutonomousTask

    agent = _agent(workspace, with_tests=False)

    seen: dict[str, frozenset[str]] = {}

    def _capture(*, user_question: str) -> str:
        seen["blocked"] = frozenset(agent.policy.blocked_tools)
        return "analysis"

    agent.run = _capture  # type: ignore[method-assign]
    runtime = AutonomousRuntime(agent, workspace=workspace)
    task = AutonomousTask(kind="goal", description="analyze core/replan.py")

    # Dry-run: effect tools are blocked during the run.
    report = runtime._task_goal(task, AutonomousRuntimeConfig(dry_run=True))
    assert report.status == "done"
    assert {"file_write", "shell_exec"} <= seen["blocked"]
    # And the block-list is restored once the run finishes.
    assert agent.policy.blocked_tools == frozenset()

    # Non-dry-run: effect tools are NOT blocked (the loop's own approval
    # gate governs them instead).
    seen.clear()
    runtime._task_goal(task, AutonomousRuntimeConfig(dry_run=False))
    assert "file_write" not in seen["blocked"]
    assert agent.policy.blocked_tools == frozenset()


def test_policy_gate_blocks_listed_tool(workspace: Path):
    """PolicyGate denies any tool named in its blocked_tools set, before
    risk evaluation, regardless of that tool's reversibility."""
    from core.models import Action

    registry = ToolRegistry()
    registry.register(FakeRunTestsTool())
    gate = PolicyGate(registry)

    allow = gate.check(
        Action(step_id="s1", type="tool_call", tool_name="run_tests", parameters={})
    )
    assert allow.decision == "allow"

    gate.blocked_tools = frozenset({"run_tests"})
    deny = gate.check(
        Action(step_id="s1", type="tool_call", tool_name="run_tests", parameters={})
    )
    assert deny.decision == "deny"
    assert "blocked in this context" in " ".join(deny.reasons)


# ── Reflection freeze: operator brake reaches the reflection side channel ────

def _agent_with_policy(
    workspace: Path, *, write_policy: MemoryWritePolicy, llm_responses: list[str]
) -> AgentLoop:
    registry = ToolRegistry()
    llm = FakeLLM(responses=llm_responses)
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=write_policy,
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )


def _seed_failure_logs(workspace: Path) -> None:
    """Write logs with a repeating tool failure so reflection synthesises a lesson."""
    import json as _json

    log_dir = workspace / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"trace_id": "r1", "event": "tool_call", "payload": {"id": "tc1", "tool_name": "web_fetch"}},
        {"trace_id": "r1", "event": "tool_result", "payload": {"tool_call_id": "tc1", "status": "error", "error": "timeout"}},
        {"trace_id": "r2", "event": "tool_call", "payload": {"id": "tc2", "tool_name": "web_fetch"}},
        {"trace_id": "r2", "event": "tool_result", "payload": {"tool_call_id": "tc2", "status": "error", "error": "timeout"}},
    ]
    (log_dir / "runs.jsonl").write_text(
        "\n".join(_json.dumps(e) for e in events), encoding="utf-8"
    )


_LESSONS_JSON = (
    '[{"insight": "web_fetch keeps timing out; add retry/backoff", '
    '"action": "repair", "focus_area": "tools/web_fetch.py", "confidence": 0.9}]'
)


def test_run_reflection_honors_frozen_write_policy(workspace: Path):
    """When the operator brake froze 'agent-auto' writes, reflection lessons
    (agent-initiated memory growth) must NOT reach the persistent store."""
    _seed_failure_logs(workspace)
    agent = _agent_with_policy(
        workspace,
        write_policy=MemoryWritePolicy(frozen_sources={"agent-auto"}),
        llm_responses=[_LESSONS_JSON],
    )
    runtime = AutonomousRuntime(agent, workspace=workspace)

    result = runtime._run_reflection(AutonomousRuntimeConfig(limit=1, dry_run=True))

    assert result is not None
    # A lesson was synthesised (honest visibility) ...
    assert len(result["lessons"]) == 1
    # ... but nothing was persisted because the brake is active.
    assert result["memory_records_saved"] == 0
    assert agent.persistent_store.load() == []


def test_run_reflection_saves_with_default_policy(workspace: Path):
    """Without the operator brake, reflection persists its lessons as before."""
    _seed_failure_logs(workspace)
    agent = _agent_with_policy(
        workspace,
        write_policy=MemoryWritePolicy(),
        llm_responses=[_LESSONS_JSON],
    )
    runtime = AutonomousRuntime(agent, workspace=workspace)

    result = runtime._run_reflection(AutonomousRuntimeConfig(limit=1, dry_run=True))

    assert result is not None
    assert len(result["lessons"]) == 1
    assert result["memory_records_saved"] == 1
    assert len(agent.persistent_store.load()) == 1

