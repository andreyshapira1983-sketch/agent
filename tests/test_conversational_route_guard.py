"""Regression: a conversational / greeting-laden turn must NOT be hijacked into
a deterministic operator command.

Side-effect of the #2 capability-check widening: "Привет. Как дела? Что ты
умеешь делать, о чём ты думаешь." contains the substring "что ты умеешь делать",
so it routed to `capability_check` and dumped a rigid operator-capabilities
report — ignoring the greeting, the "как дела" and the "о чём думаешь". A
greeting/social turn should fall through to the normal conversational path so the
agent answers naturally (it can still describe its capabilities in prose there).
"""
from __future__ import annotations

from core.operator_intent import route_operator_intent


def test_greeting_laden_capability_question_falls_through():
    text = "Привет. Как дела? Что ты умеешь делать, о чём ты думаешь."
    assert route_operator_intent(text) is None


def test_plain_greeting_falls_through():
    for text in ("привет", "Здравствуйте!", "как дела?", "hello there"):
        assert route_operator_intent(text) is None, f"{text!r} should not route"


def test_direct_capability_question_still_routes():
    # No social markers -> still a capability_check (the #2 fix is preserved).
    for text in ("что ты умеешь делать сейчас", "Проверь свои возможности", "what can you do"):
        intent = route_operator_intent(text)
        assert intent is not None and intent.kind == "capability_check", (
            f"{text!r} should still route to capability_check (got {intent})"
        )
