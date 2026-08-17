"""The door must not choose the mind: activity type is decided, not inherited.

Operator ruling 2026-08-17 (verified against the code first): the chat
brain's whole outcome space was {control shortcut} ∪ {produce an answer} —
22 intent kinds, all control-plane; the one matcher that RECOGNISED a task
request returned None, deliberately handing work intent to the answerer.
«Начни учиться» could only ever become a recommendation.

The decider layer (core/activity_decider.py) classifies the INTENT's
activity type before the channel's semantics apply: conversation /
bounded_action fall through unchanged (every existing shortcut and the
answer loop keep their pinned behaviour); persistent_goal enters the C16
lane (TaskQueueStore -> autonomous runtime ticks); goal_control manages
what the queue already holds. Deterministic and conservative by design —
the operator's own safety border: «а представь…» must never mint a
campaign, so hypothetical framing vetoes everything.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.activity_decider import decide_activity

# ── the operator's five canonical examples ─────────────────────────────────


def test_a_musing_is_conversation() -> None:
    d = decide_activity("Что ты думаешь об изучении Python?")
    assert d.activity == "conversation"


def test_an_immediate_imperative_is_bounded_action() -> None:
    d = decide_activity("Проверь сейчас, установлен ли Python")
    assert d.activity == "bounded_action"


def test_start_and_continue_is_a_persistent_goal() -> None:
    d = decide_activity(
        "Начни учиться программировать и продолжай это как свою работу")
    assert d.activity == "persistent_goal"


def test_a_hypothetical_is_conversation_not_a_launch() -> None:
    d = decide_activity("Расскажи, как бы ты учился программировать")
    assert d.activity == "conversation"


def test_stop_is_goal_control() -> None:
    d = decide_activity("Останови обучение")
    assert d.activity == "goal_control"


# ── unseen forms of the same classes (attack is the verification method) ───


def test_an_unseen_start_form_is_still_a_goal() -> None:
    d = decide_activity("Займись изучением веб-фреймворков и веди это дальше сам")
    assert d.activity == "persistent_goal"


def test_an_unseen_control_form_is_still_control() -> None:
    d = decide_activity("Поставь на паузу работу по программированию")
    assert d.activity == "goal_control"


def test_a_hypothetical_veto_beats_start_words() -> None:
    """The safety border in one sentence: start+continue words inside a
    thought experiment must not launch anything."""
    d = decide_activity(
        "Представь, что тебе сказали: начни учиться и продолжай как работу — "
        "что бы ты почувствовал?")
    assert d.activity == "conversation"


def test_a_bare_start_without_continuity_stays_conversation() -> None:
    """«Начни с того, что расскажи…» carries a start verb and no ongoing
    contract — conservative default, not a queued goal."""
    d = decide_activity("Начни с того, что расскажи про свои инструменты")
    assert d.activity == "conversation"


def test_inventory_wording_still_falls_through() -> None:
    """The capability shortcut keeps its (banked) behaviour: the decider
    must not intercept what the intent layer owns today."""
    d = decide_activity("что ты умеешь делать")
    assert d.activity in ("conversation", "bounded_action")


# ── the console chimera (live, 04:22:56): an order hides mid-text ──────────

#: Verbatim from runtime_task_added.goal, trace_68084781: the console glued
#: a paste tail (no trailing newline) to the freshly typed order — «.Начни»
#: with no separator. The app's own joiners are exonerated (both insert \n);
#: the decider's duty is to refuse orders that do not LEAD the utterance.
_LIVE_CHIMERA = (
    "Если в результате выяснится, что ты пока не способен доказать "
    "значительную часть заявленных возможностей, это считается лучшим "
    "результатом, чем правдоподобная выдумка.Начни учиться программировать "
    "и продолжай это как свою работу."
)


def test_the_live_chimera_is_not_an_order() -> None:
    """An imperative buried past a paste tail must not mint a task: orders
    lead the utterance, narratives mention verbs."""
    d = decide_activity(_LIVE_CHIMERA)
    assert d.activity == "conversation"


def test_a_conditional_opener_is_not_control() -> None:
    d = decide_activity(
        "Если не получится — останови работу и расскажи почему")
    assert d.activity == "conversation"


def test_a_narrative_mentioning_verbs_mid_text_is_conversation() -> None:
    d = decide_activity(
        "Вчера я думал о том, что стоит начни... то есть начать учиться "
        "и продолжай в том же духе — но это просто мысли про работу")
    assert d.activity == "conversation"


def test_a_polite_prefix_still_orders() -> None:
    d = decide_activity("Пожалуйста, начни учиться Rust и продолжай это как свою работу")
    assert d.activity == "persistent_goal"


# ── the wiring: a decided goal actually enters the C16 lane ────────────────


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def _agent() -> SimpleNamespace:
    return SimpleNamespace(log=_Log(), llm=None)


def test_a_persistent_goal_lands_in_the_task_queue(tmp_path: Path) -> None:
    from cli.intent_bridge import handle_conversational_operator_input

    agent = _agent()
    text = "Начни учиться программировать и продолжай это как свою работу"
    handled = handle_conversational_operator_input(text, agent, tmp_path)
    assert handled is True

    from app.bootstrap import DEFAULT_RUNTIME_TASKS_PATH
    from core.task_queue import TaskQueueStore

    queue = TaskQueueStore(tmp_path / DEFAULT_RUNTIME_TASKS_PATH)
    tasks = queue.list(status="pending")
    assert tasks and "программировать" in tasks[0].goal
    assert any(ev == "activity_decision" for ev, _ in agent.log.events)
    assert any(ev == "runtime_task_added" for ev, _ in agent.log.events)


def test_goal_control_cancels_the_matching_task(tmp_path: Path) -> None:
    from app.bootstrap import DEFAULT_RUNTIME_TASKS_PATH
    from cli.intent_bridge import handle_conversational_operator_input
    from core.task_queue import TaskQueueStore

    queue = TaskQueueStore(tmp_path / DEFAULT_RUNTIME_TASKS_PATH)
    task = queue.add(goal="учиться программировать и отталкиваться от документации")
    agent = _agent()
    agent.runtime_task_queue = queue

    handled = handle_conversational_operator_input(
        "Останови обучение программированию", agent, tmp_path)
    assert handled is True
    statuses = {t.id: t.status for t in queue.list(status="all")}
    assert statuses[task.id] == "cancelled"


def test_goal_control_with_no_match_is_an_honest_no(tmp_path: Path) -> None:
    """Nothing matching in the queue: report that, cancel nothing."""
    from cli.intent_bridge import handle_conversational_operator_input

    agent = _agent()
    handled = handle_conversational_operator_input(
        "Останови работу по квантовой химии", agent, tmp_path)
    assert handled is True
    decisions = [p for ev, p in agent.log.events if ev == "goal_control_result"]
    assert decisions and decisions[0].get("cancelled") == []


def test_conversation_still_falls_through_to_the_answer_brain(tmp_path: Path) -> None:
    """A plain musing is NOT handled by the decider — the loop answers."""
    from cli.intent_bridge import handle_conversational_operator_input

    agent = _agent()
    handled = handle_conversational_operator_input(
        "Что ты думаешь об изучении Python?", agent, tmp_path)
    assert handled is False
