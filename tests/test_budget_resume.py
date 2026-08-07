from __future__ import annotations

import json
from pathlib import Path

from app.budget_guard import _run_agent_with_budget_guard
from cli.command_dispatch import handle_meta_command
from cli.resume import _resume_question_from_checkpoint
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.model_router import ModelRouter
from core.model_usage import ModelUsageLedger, ModelUsageLimits
from core.policy import PolicyGate
from core.task_queue import TaskQueueStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry


def _build_guarded_agent(
    workspace: Path,
    llm: FakeLLM,
    limits: ModelUsageLimits,
    *,
    preused_calls: int = 0,
    verifier_enabled: bool = False,
    max_replan_attempts: int = 1,
) -> AgentLoop:
    registry = ToolRegistry()
    ledger = ModelUsageLedger(
        path=workspace / "data" / "model_usage.jsonl",
        limits=limits,
    )
    for _ in range(preused_calls):
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
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        model_router=router,
        memory=None,
        persistent_store=None,
        source_registry_store=None,
        max_replan_attempts=max_replan_attempts,
        verifier_enabled=verifier_enabled,
        clarification_enabled=False,
        odd_enabled=False,
    )


def _agent_with_exhausted_model_budget(workspace: Path) -> tuple[AgentLoop, FakeLLM]:
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    agent = _build_guarded_agent(
        workspace, llm, ModelUsageLimits(max_calls=1), preused_calls=1
    )
    return agent, llm


def _agent_with_free_budget(workspace: Path) -> AgentLoop:
    """A fresh agent whose run can complete: planner + synthesizer both fit."""
    llm = FakeLLM(
        responses=['{"reasoning":"no tools","sources":[]}', "resumed answer text"]
    )
    return _build_guarded_agent(workspace, llm, ModelUsageLimits())


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


def test_budget_stop_in_synthesis_saves_the_synthesis_phase(workspace: Path):
    """The catch site at loop_synthesis:684, reached for the first time.

    Budget of one call: the planner spends it, the synthesizer is denied
    before its LLM call. The rich in-cycle checkpoint must name the phase —
    without the catch site the guard's thin fallback would say budget_guard.
    """
    from core.checkpoint import CheckpointLoader

    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    agent = _build_guarded_agent(workspace, llm, ModelUsageLimits(max_calls=1))

    answer = _run_agent_with_budget_guard(
        agent, user_question="Explain the repository status",
        workspace=workspace, stream=False,
    )

    assert answer.startswith("Model budget exceeded")
    ctx = CheckpointLoader(workspace / "logs").load(agent.log.trace_id)
    assert ctx is not None and ctx.last_phase == "paused"
    assert ctx.paused["current_phase"] == "synthesis"
    assert ctx.paused["blocked_model"]["role"] == "synthesizer"
    queue = TaskQueueStore(workspace / "data" / "runtime_tasks.jsonl")
    assert len(queue.list(status="paused")) == 1


def test_budget_stop_in_verify_replan_saves_the_verification_phase(workspace: Path):
    """The catch site at loop_verify_replan:328, reached for the first time.

    Two calls fit (planner, synthesizer); the synthesized answer cites a web
    URL with no matching evidence, so the verify loop asks the planner for a
    fetch plan — and that third call is denied.
    """
    from core.checkpoint import CheckpointLoader

    llm = FakeLLM(
        responses=[
            '{"reasoning":"no tools","sources":[]}',
            "The fact [web:http://example.com/a] holds.",
        ]
    )
    agent = _build_guarded_agent(
        workspace,
        llm,
        ModelUsageLimits(max_calls=2),
        verifier_enabled=True,
        # Room to replan: with a budget of 1 the policy aborts the verify loop
        # (abort_exhausted) before the planner call this test needs to reach.
        max_replan_attempts=3,
    )

    answer = _run_agent_with_budget_guard(
        agent, user_question="Explain the repository status",
        workspace=workspace, stream=False,
    )

    assert answer.startswith("Model budget exceeded")
    ctx = CheckpointLoader(workspace / "logs").load(agent.log.trace_id)
    assert ctx is not None and ctx.last_phase == "paused"
    assert ctx.paused["current_phase"] == "verification_replan"
    assert ctx.paused["blocked_model"]["role"] == "planner"


def test_successful_resume_retires_the_paused_task(workspace: Path):
    """The back half of the pause arc: success must close what the stop opened.

    Measured 2026-08-08 before this test existed: pause -> resume -> success
    left the task `paused` forever, because nothing wrote paused->done and the
    resumed run carries a fresh trace_id that joins to nothing. The join is
    therefore carried explicitly: `resolve_resume` names the paused trace it
    restored, and the guard retires that task once the resumed run completes
    without a new budget stop.
    """
    from cli.resume import resolve_resume

    agent, _llm = _agent_with_exhausted_model_budget(workspace)
    _run_agent_with_budget_guard(
        agent,
        user_question="Explain the repository status",
        workspace=workspace,
        stream=False,
    )
    queue = TaskQueueStore(workspace / "data" / "runtime_tasks.jsonl")
    assert queue.list(status="paused"), "precondition: the stop queued a paused task"

    decision = resolve_resume(
        agent.log.trace_id, workspace=workspace, ask=None, file_hint=None
    )
    assert decision.ask and "Resume the interrupted task" in decision.ask
    assert decision.resumed_paused_trace == agent.log.trace_id, (
        "the decision must name the paused trace it restored — "
        "this is the only join between the old task and the new run"
    )

    resumed = _agent_with_free_budget(workspace)
    answer = _run_agent_with_budget_guard(
        resumed,
        user_question=decision.ask,
        workspace=workspace,
        stream=False,
        resumed_from=decision.resumed_paused_trace,
    )
    assert not answer.startswith("Model budget exceeded")

    assert queue.list(status="paused") == [], (
        "the resumed run completed; its pause record may not stay resumable"
    )
    done = queue.list(status="done")
    assert len(done) == 1 and done[0].kind == "resume_checkpoint"
    assert done[0].last_report["resumed_by"] == resumed.log.trace_id


def test_resume_that_pauses_again_keeps_the_old_task(workspace: Path):
    """The other pole: a resume that hits the budget again retires nothing.

    The work is still not done, so the old pause record must stay truthful —
    and the second stop queues its own task under the fresh trace.
    """
    from cli.resume import resolve_resume

    agent, _llm = _agent_with_exhausted_model_budget(workspace)
    _run_agent_with_budget_guard(
        agent,
        user_question="Explain the repository status",
        workspace=workspace,
        stream=False,
    )
    queue = TaskQueueStore(workspace / "data" / "runtime_tasks.jsonl")
    old_ids = {t.id for t in queue.list(status="paused")}
    assert old_ids

    decision = resolve_resume(
        agent.log.trace_id, workspace=workspace, ask=None, file_hint=None
    )
    resumed, _llm2 = _agent_with_exhausted_model_budget(workspace)
    answer = _run_agent_with_budget_guard(
        resumed,
        user_question=decision.ask,
        workspace=workspace,
        stream=False,
        resumed_from=decision.resumed_paused_trace,
    )
    assert answer.startswith("Model budget exceeded")

    paused_now = {t.id for t in queue.list(status="paused")}
    assert old_ids <= paused_now, "an unfinished pause may not be retired"
    assert queue.list(status="done") == []


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
