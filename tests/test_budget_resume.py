from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    extra_tools: list | None = None,
) -> AgentLoop:
    registry = ToolRegistry()
    for tool in extra_tools or []:
        registry.register(tool)
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


def test_completed_cycle_checkpoints_its_plan_and_executed_steps(workspace: Path):
    """The plan/act checkpoint wires through the real loop, bitten at last.

    Their reader is thin but real: the act rows become `ctx.artifacts`, which
    the `--resume` replay notice prints; the plan rows become `ctx.attempt`.
    Measured 2026-08-08: with BOTH save calls cut, 73 checkpoint/resume/
    integration tests stayed green — nothing had ever driven these rows
    through the loop and read them back.
    """
    import json as _json

    from core.checkpoint import CheckpointLoader
    from tools.file_read import FileReadTool

    (workspace / "doc.txt").write_text("alpha beta gamma\n", encoding="utf-8")
    plan_json = _json.dumps(
        {
            "reasoning": "read the named file",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "doc.txt"},
                    "rationale": "the question names it",
                }
            ],
        }
    )
    llm = FakeLLM(
        responses=[
            plan_json,
            ("Conclusion: file read. [file:doc.txt]\nFacts:\n- alpha [file:doc.txt]\n"
            "Sources:\n1. file:doc.txt\nConfidence: high\n"),
        ]
    )
    agent = _build_guarded_agent(
        workspace, llm, ModelUsageLimits(), extra_tools=[FileReadTool(workspace_root=workspace)]
    )

    agent.run(user_question="прочитай doc.txt и перескажи")

    ctx = CheckpointLoader(workspace / "logs").load(agent.log.trace_id)
    assert ctx is not None
    assert ctx.attempt >= 1, "the plan row must carry the attempt number"
    assert any(
        (v or {}).get("tool") == "file_read" for v in ctx.artifacts.values()
    ), (
        f"the executed step must survive as an act row -> ctx.artifacts, "
        f"got {ctx.artifacts!r}"
    )


def _agent_with_exhausted_model_budget(
    workspace: Path, *, gateway_path: str = "repl",
) -> tuple[AgentLoop, FakeLLM]:
    """`gateway_path` — на каком пути шёл прерванный ход.

    С 2026-08-15 очередь работ принимает только непригляданную работу: реплику
    оператор наберёт заново, а очередь кормит автономный режим (см.
    `core.task_queue.checkpoint_is_resumable_work`). Контрольная точка пишется
    на любом пути, и `--resume <trace_id>` читает именно её, а не очередь.
    """
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    agent = _build_guarded_agent(
        workspace, llm, ModelUsageLimits(max_calls=1), preused_calls=1
    )
    agent.gateway_path = gateway_path
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

    # Ход шёл в диалоге, и в очередь РАБОТ он не попадает: она кормит
    # автономный режим, а прерванную реплику оператор наберёт заново. Точка
    # выше проверена — возобновление через `--resume` читает её, не очередь.
    queue = TaskQueueStore(workspace / "data" / "runtime_tasks.jsonl")
    assert queue.list(status="paused") == []
    # Что припаркованный отчёт несёт trace_id и причину остановки, проверяется
    # там, где строка теперь и возникает:
    # tests/test_the_work_queue_is_not_a_chat_log.py.

    # Подсказку про `--resume` печатал `:task-list` из очереди — из той самой
    # строки, которой у диалога больше нет. Её печатает сам страж, иначе
    # оператор потерял бы единственное место, где узнавал о возможности.
    assert handle_meta_command(":task-list paused", agent, workspace) is True
    assert handle_meta_command(":queue-status", agent, workspace) is True
    out = capsys.readouterr()
    assert f"--resume {agent.log.trace_id}" in out.err
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
    assert queue.list(status="paused") == []  # диалог, не работа


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


def test_streamed_cycle_bills_the_synthesizer_too(workspace: Path):
    """The streamed path wears the same budget wrapper as the plain one.

    Measured 2026-08-08 before the fix: a streamed cycle left only the
    planner record — stream_complete fell through __getattr__ to the raw
    provider LLM, past assert_can_start and record. Streaming is the REPL
    default, so this is the primary interactive mode's billing.
    """
    from tests.conftest import StreamingFakeLLM

    llm = StreamingFakeLLM(
        responses=['{"reasoning":"no tools","sources":[]}', "streamed answer body"]
    )
    agent = _build_guarded_agent(workspace, llm, ModelUsageLimits())
    tokens: list[str] = []

    answer = agent.run(
        user_question="Explain the repository status", on_token=tokens.append
    )

    assert tokens and "".join(tokens).strip() == answer.strip()
    ledger = ModelUsageLedger(path=workspace / "data" / "model_usage.jsonl")
    roles = [r.role for r in ledger.load_records()]
    assert roles == ["planner", "synthesizer"], (
        f"streamed synthesis must be billed like plain synthesis, got {roles}"
    )


def test_streamed_cycle_is_blocked_and_paused_on_an_exhausted_budget(workspace: Path):
    """The synthesis pause arc must exist in streamed mode too.

    Before the fix the streamed run COMPLETED on a budget of one call —
    no pre-flight ran, so no ModelBudgetExceeded, no pause checkpoint, no
    resumable task, while the non-streamed run was blocked and paused.
    """
    from core.checkpoint import CheckpointLoader
    from core.model_usage import ModelBudgetExceeded
    from tests.conftest import StreamingFakeLLM

    llm = StreamingFakeLLM(
        responses=['{"reasoning":"no tools","sources":[]}', "never streamed"]
    )
    agent = _build_guarded_agent(workspace, llm, ModelUsageLimits(max_calls=1))
    tokens: list[str] = []

    with pytest.raises(ModelBudgetExceeded):
        agent.run(
            user_question="Explain the repository status", on_token=tokens.append
        )

    assert tokens == [], "no token may stream once the budget is exhausted"
    ctx = CheckpointLoader(workspace / "logs").load(agent.log.trace_id)
    assert ctx is not None and ctx.last_phase == "paused"
    assert ctx.paused["current_phase"] == "synthesis"
    assert ctx.paused["blocked_model"]["role"] == "synthesizer"


def test_completed_cycle_checkpoints_the_answer_it_returned(workspace: Path):
    """The respond checkpoint must carry the REAL answer, not a shape of one.

    `--resume <trace>` of a completed run replays `ctx.answer` verbatim
    (cli/resume.py branch 4). Measured 2026-08-08: blanking the answer at the
    loop's save_respond call left 242 checkpoint/resume/cli tests green —
    every replay test wrote its checkpoint by hand, never through the loop.
    """
    from core.checkpoint import CheckpointLoader

    llm = FakeLLM(
        responses=['{"reasoning":"no tools","sources":[]}', "checkpointed answer body"]
    )
    agent = _build_guarded_agent(workspace, llm, ModelUsageLimits())

    answer = agent.run(user_question="Explain the repository status")

    ctx = CheckpointLoader(workspace / "logs").load(agent.log.trace_id)
    assert ctx is not None, "a completed cycle must leave a loadable checkpoint"
    assert answer.strip() and ctx.answer == answer, (
        "the replay contract: what --resume would print must be what run() returned"
    )
    assert ctx.question == "Explain the repository status"


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

    # Непригляданный путь: именно ради него очередь и заведена — за
    # прерванным `:auto-run` никто не следит и сам его не перезапустит.
    agent, _llm = _agent_with_exhausted_model_budget(workspace, gateway_path="runtime")
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

    # Непригляданный путь: именно ради него очередь и заведена — за
    # прерванным `:auto-run` никто не следит и сам его не перезапустит.
    agent, _llm = _agent_with_exhausted_model_budget(workspace, gateway_path="runtime")
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
