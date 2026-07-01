from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.model_router import ModelRouter
from core.model_usage import ModelUsageLedger, ModelUsageLimits
from core.policy import PolicyGate
from core.task_queue import TaskQueueStore
from main import (
    _resume_question_from_checkpoint,
    _run_agent_with_budget_guard,
    handle_meta_command,
)
from tests.conftest import FakeLLM
from tools.base import ToolRegistry


def _agent_with_exhausted_model_budget(workspace: Path) -> tuple[AgentLoop, FakeLLM]:
    registry = ToolRegistry()
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    ledger = ModelUsageLedger(
        path=workspace / "data" / "model_usage.jsonl",
        limits=ModelUsageLimits(max_calls=1),
    )
    ledger.record(
        role="bootstrap",
        provider="fake",
        model="fake-1",
        route_reason="test",
        cost_tier="low",
        status="success",
        input_tokens=1,
        output_tokens=1,
        estimated=True,
        started_at="2026-06-29T00:00:00+00:00",
        completed_at="2026-06-29T00:00:00+00:00",
        duration_ms=1,
    )
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda _provider, _model: llm,
        usage_ledger=ledger,
    )
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        model_router=router,
        memory=None,
        persistent_store=None,
        source_registry_store=None,
        max_replan_attempts=1,
        verifier_enabled=False,
        clarification_enabled=False,
        odd_enabled=False,
    )
    return agent, llm


def test_budget_denial_before_planner_persists_resumable_checkpoint_and_task(
    workspace: Path,
    capsys,
):
    agent, llm = _agent_with_exhausted_model_budget(workspace)

    answer = _run_agent_with_budget_guard(
        agent,
        user_question="Explain the repository status",
        workspace=workspace,
        stream=False,
    )

    assert answer.startswith("Model budget exceeded: model call budget exhausted")
    assert llm.calls == []

    checkpoint_path = workspace / "logs" / f"checkpoints_{agent.log.trace_id}.jsonl"
    rows = [
        json.loads(line)
        for line in checkpoint_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["phase"] for row in rows] == ["observe", "paused"]
    paused = rows[-1]["data"]
    assert paused["stop_reason"] == "budget_exhausted"
    assert paused["original_user_question"] == "Explain the repository status"
    assert paused["current_phase"] == "planning"
    assert paused["planned_steps"] == []
    assert paused["completed_steps"] == []
    assert paused["remaining_steps"] == []
    assert paused["blocked_model"]["counter"] == "llm_calls"
    assert paused["blocked_model"]["role"] == "planner"
    assert paused["blocked_model"]["provider"] == "fake"
    assert paused["blocked_model"]["model"] == "fake-1"
    assert paused["blocked_model"]["used"] == 1
    assert paused["blocked_model"]["limit"] == 1
    assert paused["timestamp"]

    queue = TaskQueueStore(workspace / "data" / "runtime_tasks.jsonl")
    tasks = queue.list(status="paused")
    assert len(tasks) == 1
    assert tasks[0].kind == "resume_checkpoint"
    assert tasks[0].last_report is not None
    assert tasks[0].last_report["trace_id"] == agent.log.trace_id
    assert tasks[0].last_report["stop_reason"] == "budget_exhausted"

    assert handle_meta_command(":task-list paused", agent, workspace) is True
    assert handle_meta_command(":queue-status", agent, workspace) is True
    out = capsys.readouterr()
    assert f"resume={agent.log.trace_id}" in out.err
    assert '"resumable"' in out.err
    assert agent.log.path.read_text(encoding="utf-8").count("model_budget_blocked") == 1


def test_successful_budget_guard_creates_no_resumable_item(
    workspace: Path,
    monkeypatch,
):
    agent, _llm = _agent_with_exhausted_model_budget(workspace)
    monkeypatch.setattr(agent, "run", lambda **_kwargs: "ok")

    answer = _run_agent_with_budget_guard(
        agent,
        user_question="hello",
        workspace=workspace,
        stream=False,
    )

    assert answer == "ok"
    assert not (workspace / "data" / "runtime_tasks.jsonl").exists()
    checkpoint_path = workspace / "logs" / f"checkpoints_{agent.log.trace_id}.jsonl"
    assert not checkpoint_path.exists()


def test_resume_prompt_includes_saved_budget_context(workspace: Path):
    agent, _llm = _agent_with_exhausted_model_budget(workspace)
    _run_agent_with_budget_guard(
        agent,
        user_question="Explain the repository status",
        workspace=workspace,
        stream=False,
    )

    from core.checkpoint import CheckpointLoader

    ctx = CheckpointLoader(workspace / "logs").load(agent.log.trace_id)
    prompt = _resume_question_from_checkpoint(ctx)

    assert "Resume the interrupted task" in prompt
    assert "Explain the repository status" in prompt
    assert "budget_exhausted" in prompt
    assert '"counter": "llm_calls"' in prompt
