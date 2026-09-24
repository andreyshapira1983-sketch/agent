"""Леджер считает исходы, а не события — девять швов одной болезни.

Аудит 2026-09-03 (AUTONOMY_AUDIT_2026-09-03.md in git history, раздел 1), каждый
шов проверен построчно. Слово «сделано / полезно / успех / executed» чеканилось
из СОБЫТИЯ («задача вернулась», «в ящике что-то лежит», «ответ непустой»,
«полоса дошла до конца»), а не из ИСХОДА. Живая цена: утренний ремонт Д1
(вопрос ворот → статус clarify) был перекрыт тремя из этих швов и не изменил ни
одной строки леджера.

Швы и их свидетели ниже:
  A1 служебная задача status всегда done → semantic_result всегда «работа»;
  A2 строка очереди задач помечается done по вердикту прерывателя, минуя
     semantic_result;
  A3 proposal = абсолютный счёт ящика одобрений, а не дельта этого прогона;
  A4 artifact из любого непустого ответа, включая встречный вопрос;
  A5 столкновение dedup возвращается как предложение;
  A7 откат полосы — «терминал» → mark_executed (и очередь value-review
     называет откат «applied»);
  A8 clarify → record_success прерывателя;
  A9 пустой ответ → done.
Положительные контроли: настоящая работа по-прежнему считается работой.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.approval import AutoApprover
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.autonomous_runtime_types import AutonomousRunReport, AutonomousTaskReport
from core.circuit_breaker import CircuitBreaker
from core.clarification_policy import ClarificationResult
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from core.task_lifecycle import classify_run_outcome
from tests.conftest import FakeLLM
from tools.base import ToolRegistry

_QUESTION = "Я не могу безопасно продолжить. Уточни: что именно нужно построить?"


def _task(kind: str, status: str, answer: str | None = None) -> AutonomousTaskReport:
    details = {"answer": answer} if answer is not None else {}
    return AutonomousTaskReport(AutonomousTask(kind, f"{kind} task"), status, "s", details)


def _report(*tasks: AutonomousTaskReport, status: str = "completed") -> AutonomousRunReport:
    return AutonomousRunReport(
        status=status, dry_run=True, goal="g", tasks=list(tasks),
        budget={}, circuit={}, approvals={},
    )


# ── A1 ──────────────────────────────────────────────────────────────────────

def test_a_status_probe_is_not_work():
    """Каждая очередь начинается со status-задачи, и она всегда done."""
    report = _report(_task("status", "done"), _task("goal", "clarify", _QUESTION))

    assert report.semantic_result() == ("empty", False), (
        "служебная задача status не работа; вопрос ворот — тоже"
    )


def test_a_learn_pass_alone_is_not_work_either():
    report = _report(_task("status", "done"), _task("learn", "done"), _task("goal", "failed"))

    assert report.semantic_result()[1] is False


def test_real_goal_work_still_counts():
    """Положительный контроль A1."""
    report = _report(_task("status", "done"), _task("goal", "done", "a real answer"))

    assert report.semantic_result() == ("completed", True)


# ── A2 ──────────────────────────────────────────────────────────────────────

def test_a_completed_run_without_work_does_not_mark_the_queue_row_done():
    decision = classify_run_outcome(status="completed", stop_reason="", work_done=False)

    assert decision.outcome != "done"
    assert "work" in decision.reason


def test_a_completed_run_with_work_still_marks_done():
    """Положительный контроль A2."""
    assert classify_run_outcome(status="completed", work_done=True).outcome == "done"


# ── A3 / A4 — на шве campaign_io._default_execute_action ─────────────────────

class _FakeRuntime:
    """Прогон, который ничего не подал и ответил встречным вопросом."""

    report = None

    def __init__(self, agent, *, workspace, approval_inbox=None):
        self.agent, self.workspace = agent, workspace

    def run(self, config):
        return type(self).report


def _execute_with_fake_runtime(monkeypatch, tmp_path, report, inbox):
    import core.autonomous_runtime as rt
    from core.campaign_io import _default_execute_action
    from core.campaign_types import CampaignConfig

    _FakeRuntime.report = report
    monkeypatch.setattr(rt, "AutonomousRuntime", _FakeRuntime)
    action = SimpleNamespace(
        action="investigate_something", title="t", reason="r", evidence=(),
        target_path=None,
    )
    return _default_execute_action(
        agent=SimpleNamespace(model_router=None, log=None, llm=None),
        workspace=tmp_path, action=action,
        config=CampaignConfig(goal="g", max_cycles=1, dry_run=True),
        approval_inbox=inbox,
    )


def test_old_inbox_items_are_not_this_cycles_proposal(monkeypatch, tmp_path):
    """A3: три чужие заявки лежат со вчера; прогон не подал ничего."""
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    for i in range(3):
        inbox.add(operation="self_apply_lane.run", summary=f"old {i}")
    report = _report(_task("status", "done"), _task("goal", "done", "answer"))
    report.approvals = inbox.snapshot()

    outcome = _execute_with_fake_runtime(monkeypatch, tmp_path, report, inbox)

    assert outcome.proposal is None, f"чужие заявки записаны как предложение: {outcome.proposal!r}"


def test_a_question_back_is_not_an_artifact(monkeypatch, tmp_path):
    """A4: вопрос ворот — не продукт цикла."""
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    report = _report(_task("status", "done"), _task("goal", "clarify", _QUESTION))
    report.approvals = inbox.snapshot()

    outcome = _execute_with_fake_runtime(monkeypatch, tmp_path, report, inbox)

    assert outcome.artifact is None
    assert outcome.did_work is False


def test_a_real_answer_is_still_an_artifact(monkeypatch, tmp_path):
    """Положительный контроль A4."""
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    report = _report(_task("status", "done"), _task("goal", "done", "the measured answer"))
    report.approvals = inbox.snapshot()

    outcome = _execute_with_fake_runtime(monkeypatch, tmp_path, report, inbox)

    assert outcome.artifact == "reasoning: the measured answer"
    assert outcome.did_work is True


# ── A5 ──────────────────────────────────────────────────────────────────────

def test_a_dedup_collision_is_not_a_proposal(tmp_path):
    from core.campaign_io import _propose_doctrine_draft

    target = "knowledge/doctrine/future/NEW_CONTRACT.md"
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    inbox.add(operation="self_apply_lane.run", summary="earlier draft",
              dedup_key=f"self_apply:{target}:campaign_doctrine_draft")
    agent = SimpleNamespace(
        log=None,
        llm=SimpleNamespace(complete=lambda **_k: "# NEW_CONTRACT\n\nA second draft of the same document.\n"),
    )

    result = _propose_doctrine_draft(
        agent=agent, workspace=tmp_path, goal=f"Draft {target} per the charter",
        approval_inbox=inbox,
    )

    assert result is None, f"столкновение записано как предложение: {result!r}"
    assert len(inbox.pending()) == 1


# ── A7 ──────────────────────────────────────────────────────────────────────

def test_a_rolled_back_lane_run_is_aborted_not_executed(tmp_path):
    from core.self_apply_bridge import (
        SELF_APPLY_OPERATION,
        build_self_apply_payload,
        run_approved_self_apply,
    )
    from core.self_apply_lane import SelfApplyReport

    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    payload = build_self_apply_payload(
        files=[{"path": "core/example.py", "content": "x = 1\n"}],
        reason="fix example", evidence=["log line A"],
        test_paths=["tests/test_example.py"], origin="repair",
    )
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="a patch", payload=payload)
    inbox.approve(item.id, actor="test")

    def _lane(*_a, **_kw):
        return SelfApplyReport(status="rolled_back", reason="full pytest failed",
                               branch="self-apply/test", rollback_status="restored")

    result = run_approved_self_apply(
        inbox=inbox, item_id=item.id, workspace=tmp_path, vcs=object(), test_runner=object(),
        lane=_lane,
    )
    assert result["status"] == "rolled_back", result

    assert inbox.get(item.id).status == "aborted", (
        "откат — попытка, а не применение: value-review не должен звать её applied"
    )


# ── A8 / A9 — на живой петле ────────────────────────────────────────────────

def _agent(workspace: Path) -> AgentLoop:
    registry = ToolRegistry()
    llm = FakeLLM(responses=[])
    return AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry), memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
        retrieval_policy=MemoryRetrievalPolicy(), write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"), max_replan_attempts=1,
    )


def test_a_question_back_does_not_credit_the_circuit_breaker(workspace: Path):
    agent = _agent(workspace)
    agent.clarification_enabled = True
    agent._check_clarification = lambda _q: ClarificationResult(decision="ask", question=_QUESTION)  # type: ignore[method-assign]
    circuit = CircuitBreaker()
    circuit.record_failure("earlier failure")
    assert circuit.consecutive_failures == 1

    AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(goal="analyze core/replan.py", include_goal=True,
                                include_tests=False, dry_run=True, limit=3),
        circuit=circuit,
    )

    assert circuit.consecutive_failures == 1, (
        "вопрос не успех: прерыватель не должен сбрасывать счёт провалов"
    )


def test_an_empty_answer_is_not_done(workspace: Path):
    agent = _agent(workspace)
    agent.run = lambda *, user_question: ""  # type: ignore[method-assign]
    runtime = AutonomousRuntime(agent, workspace=workspace)

    report = runtime._task_goal(AutonomousTask("goal", "analyze core/replan.py"),
                                AutonomousRuntimeConfig(dry_run=True))

    assert report.status == "inconclusive"
