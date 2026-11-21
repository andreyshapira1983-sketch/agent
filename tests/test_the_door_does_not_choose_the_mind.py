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

# ── the console chimera (live, 04:22:56): an order hides mid-text ──────────


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
