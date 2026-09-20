"""A draft that answers something else earns exactly one rewrite.

Live dialogue 2026-09-20. Asked «which condition was not met last night», the
agent listed four conditions, found the answer among its OWN facts («the drive
goal carries propose_engineering_task, not improve_failure_to_idea_pipeline»),
wrote «данных нет» in the conclusion — and its own check printed «Соответствие
вопросу: 0.22». The number was measured, shown to the operator and banked in the
episode, and nothing acted on it. Measuring without discriminating is the same
as not measuring.

The rewrite costs one synthesizer call over the SAME evidence: no new steps, no
network. The better of the two drafts by that same measure is the one kept.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.loop_synthesis import AgentLoopSynthesis


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch):
    """Поведение вводится переключателем; по умолчанию его нет (дом. правило)."""
    monkeypatch.setenv("AGENT_REWRITE_OFF_TOPIC", "1")

QUESTION = ("Какое условие рождения заявки self_apply_lane.run не выполнялось "
            "вчера вечером в кампании драйвов?")
OFF_TOPIC = ("Первоисточник найден: статья про дисперсионное соотношение волн "
             "на мелкой воде, формула проверена расчётом.")
ON_TOPIC = ("Условие рождения заявки self_apply_lane.run не выполнялось такое: цель "
            "драйва в кампании идёт действием propose_engineering_task, а требуется "
            "improve_failure_to_idea_pipeline — вчера вечером не совпало именно оно.")


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict, **_kw) -> None:
        self.events.append((event, payload))


def _loop(degraded: bool = False) -> AgentLoopSynthesis:
    loop = AgentLoopSynthesis.__new__(AgentLoopSynthesis)
    loop.log = _Log()
    loop._last_synth_degraded = degraded
    loop._sensor_failed = lambda *_a, **_kw: None
    return loop


def _state(draft: str) -> SimpleNamespace:
    return SimpleNamespace(draft_answer=draft, user_question=QUESTION, failure_history=[])


def test_an_off_topic_draft_is_rewritten_over_the_same_evidence() -> None:
    loop, st = _loop(), _state(OFF_TOPIC)
    asked: list[int] = []

    def _second(attempt):
        asked.append(attempt.index)
        return ON_TOPIC

    loop._rewrite_if_off_topic(st, _second)
    assert asked == [1], "ровно одна вторая сборка"
    assert st.draft_answer == ON_TOPIC
    assert [t.code for t in st.failure_history] == ["answer_off_topic"]
    assert "self_apply_lane.run" in st.failure_history[0].reason, "вопрос обязан доехать"
    event = dict(loop.log.events[0][1])
    assert loop.log.events[0][0] == "answer_off_topic" and event["rewritten"] is True
    assert event["relevance_after"] > event["relevance_before"]


def test_a_draft_on_the_question_is_left_alone() -> None:
    loop, st = _loop(), _state(ON_TOPIC)
    loop._rewrite_if_off_topic(st, lambda _a: "не должно быть вызвано")
    assert st.draft_answer == ON_TOPIC and st.failure_history == [] and loop.log.events == []


def test_a_worse_rewrite_is_discarded() -> None:
    loop, st = _loop(), _state(OFF_TOPIC)
    loop._rewrite_if_off_topic(st, lambda _a: "Погода сегодня ясная.")
    assert st.draft_answer == OFF_TOPIC, "хуже — значит не берём"
    assert loop.log.events[0][1]["rewritten"] is False


def test_a_degraded_answer_is_not_rewritten() -> None:
    """Честный обрывок собран запасным путём — переписывать нечего."""
    loop, st = _loop(degraded=True), _state(OFF_TOPIC)
    loop._rewrite_if_off_topic(st, lambda _a: ON_TOPIC)
    assert st.draft_answer == OFF_TOPIC and loop.log.events == []


def test_the_switch_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_REWRITE_OFF_TOPIC", raising=False)
    loop, st = _loop(), _state(OFF_TOPIC)
    loop._rewrite_if_off_topic(st, lambda _a: ON_TOPIC)
    assert st.draft_answer == OFF_TOPIC and loop.log.events == []


def test_a_failing_rewrite_keeps_the_first_draft() -> None:
    loop, st = _loop(), _state(OFF_TOPIC)

    def _boom(_attempt):
        raise RuntimeError("синтез упал")

    loop._rewrite_if_off_topic(st, _boom)
    assert st.draft_answer == OFF_TOPIC
