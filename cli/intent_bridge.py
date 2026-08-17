"""The bridge from plain operator language to an explicit command.

An operator does not have to type ``:commands``. A message is first run through a
**deterministic, no-LLM** matcher (``core.operator_intent``); on a positive match
for one of the softer status/capability intents the model is asked whether the
message is really a request, and it may only **cancel** the routing -- kernel
decides, model advises. Anything unmatched falls through to the normal agent loop
untouched.

A route answers *instead of* the loop, never *outside the conversation*:
``_record_routed_turn`` puts the exchange into the session record, because the
loop tail that normally writes it never runs here.

Extracted verbatim from ``main.py``. Import these names from here: the
``main.py`` re-exports that used to mirror them were removed in Phase 7, and a
fake for any of them belongs on this module, where the call sites resolve it.

Note for anyone stubbing these in a test: the functions resolve each other in
**this** module's namespace, so a stand-in has to replace the name here, not on
``main``.

``_local_operator_reply`` is the narrow no-LLM path: an explicit "reply only
with: ..." instruction that also forbids planner/synthesizer is answered locally
and never reaches a model.
"""
from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from app.operator_status import (
    _handle_autonomy_readiness,
    _handle_next_actions,
    _handle_next_safe_test,
    _handle_operator_budget,
    _handle_operator_capability_check,
    _handle_operator_check,
    _handle_operator_gaps_check,
    _handle_operator_weakness_finder,
    _handle_programming_readiness,
    _handle_urgent_status,
)
from cli.commands_approval import (
    _handle_approval_list,
    _handle_best_next_action,
)
from cli.commands_audit import _handle_architecture_audit
from cli.commands_ingest import (
    _handle_implementation_plan,
    _handle_patch_proposal_plan,
    _handle_source_review_plan,
)
from cli.commands_memory import _handle_smart_memory
from cli.commands_models import _handle_models
from cli.commands_proposals import (
    _handle_capability_request,
    _handle_subagent_proposal,
)
from cli.commands_self_build import _handle_self_build_produce
from cli.commands_self_task import _handle_self_task_propose
from core.intent_understanding import understand_intent
from core.loop import format_human_response
from core.model_router import ModelRole
from core.operator_intent import route_operator_intent
from core.strategy_router import classify_operator_strategy

if TYPE_CHECKING:  # annotations only
    from pathlib import Path

    from core.loop import AgentLoop
    from core.operator_intent import OperatorIntent

_REPLY_ONLY_STOP_RE = re.compile(
    r"\breply\s+only\s+with:\s*(?:\"([^\"]+)\"|'([^']+)'|“([^”]+)”)",
    re.IGNORECASE | re.DOTALL,
)


def _local_operator_reply(text: str, agent: AgentLoop | None = None) -> str | None:
    """Return a local response for explicit stop/ack operator instructions.

    TD-001: some operator-control messages are intentionally local and must not
    enter Planner/Synthesizer. Keep this narrow: only honour a quoted
    "Reply only with:" directive when the same instruction explicitly forbids
    the expensive model path.
    """
    normalized = " ".join((text or "").casefold().split())
    if "reply only with:" not in normalized:
        return None
    llm_stop_markers = (
        "do not call planner",
        "do not call planner or synthesizer",
        "do not use claude",
        "do not make a plan with llm",
        "не вызывай planner",
        "не вызывай synthesizer",
        "не используй claude",
        "не использовать claude",
    )
    if not any(marker in normalized for marker in llm_stop_markers):
        return None
    match = _REPLY_ONLY_STOP_RE.search(text)
    if match is None:
        return None
    answer = next((part for part in match.groups() if part), "").strip()
    if not answer:
        return None
    if agent is not None:
        agent.log.log(
            "local_operator_reply",
            {"reason": "reply_only_stop_instruction", "answer_preview": answer[:120]},
        )
    return answer


def _record_routed_turn(agent: AgentLoop, question: str, answer: str) -> None:
    """Ход, отвеченный в обход цикла, всё равно принадлежит истории сессии.

    Историю пишет хвост прогона; отвечая здесь, мы этот хвост не запускаем — и
    сообщение оператора исчезало вместе с фактом, что на него ответили
    (`docs/CODE_NOTES.md`, «Маршрут ответил — и стёр ход»).
    """
    memory = getattr(agent, "memory", None)
    if memory is None:
        return
    try:
        turn = memory.record_turn(
            question=question, planner_reasoning="", tools_used=[],
            artifact_labels=[], answer=answer,
        )
    except Exception as exc:  # noqa: BLE001 — потеря хода видна, а не молчалива
        agent.log.log("routed_turn_not_recorded", {"error": repr(exc)})
        return
    agent.log.log(
        "memory_write",
        {"session_id": memory.session_id, "turn_id": turn.id,
         "turn_index": turn.index, "tools_used": [], "labels": [],
         "answered_outside_the_loop": True},
    )


def _handle_local_operator_reply(text: str, agent: AgentLoop) -> bool:
    answer = _local_operator_reply(text, agent)
    if answer is None:
        return False
    print("\n" + format_human_response(answer) + "\n")
    _record_routed_turn(agent, text, answer)
    return True


# "Soft" status/capability intents that conversational phrasing can trip. For
# these, the keyword match is VERIFIED by the model (it tells a request apart
# from a passing mention) before dispatch. Explicit imperative intents are not
# gated.
_VERIFY_INTENTS: frozenset[str] = frozenset({
    "capability_check", "project_health", "smart_memory_status",
    "current_gaps_check", "weakness_finder", "next_safe_test",
    "best_next_action", "next_actions", "autonomy_readiness",
    "model_status", "budget_status", "approval_status", "urgent_status",
})


def _model_says_conversation(text: str, intent: OperatorIntent, agent: AgentLoop) -> bool:
    """True only when the model gives a CLEAR, parseable "this is conversation"
    verdict for a soft keyword match.

    On a confirmed action, or any uncertainty (model error / unparseable /
    ungrounded / low confidence / no model), returns False — so deterministic
    routing is preserved and the model only ever *vetoes* an obvious
    conversational false-positive (model advises, kernel decides).
    """
    try:
        llm = agent.model_router.for_role(ModelRole.PLANNER)
    except Exception:  # noqa: BLE001 — no model to consult -> keep routing
        return False
    decision = understand_intent(text, available_actions=(intent.kind,), llm=llm)
    return decision.kind == "conversation" and decision.source == "model"


def _start_persistent_goal(text: str, agent: AgentLoop, workspace: Path) -> bool:
    """A spoken ongoing goal enters the C16 lane: the runtime task queue,
    which daemon/scheduler ticks consume. Reversible by «останови …»."""
    from app.task_scheduler_cli import _task_queue_for

    task = _task_queue_for(agent, workspace).add(goal=text.strip())
    agent.log.log("runtime_task_added", task.to_dict())
    print(
        f"(цель принята в работу: {task.id}; повезёт автомат — очередь задач "
        f"C16, dry_run={task.dry_run}. Остановить: «останови …» или "
        f":task-cancel {task.id})",
        file=sys.stderr,
    )
    return True


def _control_work_goals(text: str, agent: AgentLoop, workspace: Path) -> bool:
    """Cancel queued/blocked work whose goal shares words with the order;
    an empty match is reported honestly, nothing is guessed."""
    from app.task_scheduler_cli import _task_queue_for

    queue = _task_queue_for(agent, workspace)
    order_tokens = _goal_tokens(text)
    cancelled: list[str] = []
    for task in queue.list(status="all"):
        if task.status not in ("pending", "blocked"):
            continue
        if _tokens_overlap(order_tokens, _goal_tokens(task.goal)):
            queue.cancel(task.id)
            cancelled.append(task.id)
    agent.log.log("goal_control_result", {
        "order_preview": text[:120], "cancelled": cancelled,
    })
    if cancelled:
        print(f"(остановлено: {', '.join(cancelled)})", file=sys.stderr)
    else:
        print(
            "(похожих работ в очереди нет — ничего не остановлено; "
            ":task-list покажет очередь)",
            file=sys.stderr,
        )
    return True


_GOAL_STOPWORDS = frozenset({
    "останови", "прекрати", "приостанови", "возобнови", "отмени", "работу",
    "работа", "цель", "задачу", "задача", "поставь", "паузу", "stop",
    "pause", "cancel", "resume", "начни", "продолжай", "это", "как", "свою",
})


def _goal_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_]{4,}", (text or "").casefold())
    return {w for w in words if w not in _GOAL_STOPWORDS}


def _tokens_overlap(a: set[str], b: set[str]) -> bool:
    """Shared 6-char prefix counts: «программированию» must find
    «программировать» — Russian inflection, not different subjects."""
    return any(
        x[:6] == y[:6] for x in a for y in b if len(x) >= 6 and len(y) >= 6
    )


def handle_conversational_operator_input(text: str, agent: AgentLoop, workspace: Path) -> bool:
    strategy = classify_operator_strategy(text)
    agent.log.log(
        "strategy_classified",
        {"strategy": strategy.value, "text_preview": text[:120]},
    )
    # The door must not choose the mind: activity type is decided BEFORE
    # channel semantics. persistent_goal/goal_control are handled here;
    # conversation/bounded_action fall through to the pinned routes.
    from core.activity_decider import decide_activity

    decision = decide_activity(text)
    agent.log.log("activity_decision", {
        "activity": decision.activity, "reason": decision.reason,
        "text_preview": text[:120],
    })
    if decision.activity == "persistent_goal":
        return _start_persistent_goal(text, agent, workspace)
    if decision.activity == "goal_control":
        return _control_work_goals(text, agent, workspace)
    intent = route_operator_intent(text)
    if intent is None:
        return False
    # Bridge: for a soft match, let the model VETO an obvious conversational
    # false-positive. It only suppresses on a clear "this is conversation"
    # verdict; on uncertainty the deterministic route is preserved.
    if intent.kind in _VERIFY_INTENTS and _model_says_conversation(text, intent, agent):
        agent.log.log(
            "operator_intent_suppressed",
            {"kind": intent.kind, "reason": "model judged conversation, not a request"},
        )
        return False
    agent.log.log("operator_intent", intent.to_dict())
    if intent.kind == "shell_command_hint":
        print(
            "This looks like a shell/PowerShell command. "
            "Run it in PowerShell, not inside the agent REPL.",
            file=sys.stderr,
        )
        return True
    print(
        f"(operator intent: {intent.kind}; internal={intent.command})",
        file=sys.stderr,
    )
    handled = _dispatch_operator_intent(intent, agent, workspace, original_text=text)
    if handled:
        # Не тело отчёта, а ФАКТ: о чём спросили и какая команда ответила.
        # Тело печатают обработчики в stdout, и перехват вывода сломал бы те из
        # них, что ждут ввода оператора.
        _record_routed_turn(
            agent, text,
            f"(ответ дан локальной командой {intent.command}; "
            f"её вывод — в журнале прогона, не в этом ходе)",
        )
    return handled


def _dispatch_operator_intent(
    intent: OperatorIntent,
    agent: AgentLoop,
    workspace: Path,
    *,
    original_text: str = "",
) -> bool:
    if intent.kind == "capability_request":
        return _handle_capability_request(original_text, agent, workspace)
    if intent.kind == "self_build_request":
        return _handle_self_build_produce("", agent, workspace)
    if intent.kind == "subagent_proposal":
        return _handle_subagent_proposal(original_text.strip(), agent, workspace)
    if intent.kind == "architecture_audit":
        return _handle_architecture_audit("", agent, workspace)
    if intent.kind == "self_task_proposal":
        return _handle_self_task_propose("", agent, workspace)
    if intent.kind == "safe_self_check":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "capability_check":
        return _handle_operator_capability_check(agent, workspace)
    if intent.kind == "programming_readiness":
        return _handle_programming_readiness("", agent, workspace)
    if intent.kind == "current_gaps_check":
        return _handle_operator_gaps_check(agent, workspace)
    if intent.kind == "weakness_finder":
        return _handle_operator_weakness_finder(agent, workspace)
    if intent.kind == "next_safe_test":
        return _handle_next_safe_test(agent, workspace)
    if intent.kind == "project_health":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "smart_memory_status":
        return _handle_smart_memory("", agent)
    if intent.kind == "model_status":
        return _handle_models("", agent)
    if intent.kind == "budget_status":
        return _handle_operator_budget("", agent, workspace)
    if intent.kind == "approval_status":
        return _handle_approval_list("all", agent, workspace)
    if intent.kind == "urgent_status":
        return _handle_urgent_status("", agent, workspace)
    if intent.kind == "best_next_action":
        return _handle_best_next_action("", agent, workspace)
    if intent.kind == "next_actions":
        return _handle_next_actions("", agent, workspace)
    if intent.kind == "autonomy_readiness":
        return _handle_autonomy_readiness("", agent, workspace)
    if intent.kind == "source_review_plan":
        return _handle_source_review_plan(original_text, agent, workspace)
    if intent.kind == "implementation_plan":
        return _handle_implementation_plan(original_text, agent, workspace)
    if intent.kind == "patch_proposal":
        return _handle_patch_proposal_plan(original_text, agent, workspace)
    return False
