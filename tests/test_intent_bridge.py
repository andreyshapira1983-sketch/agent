"""Step 2: the bridge — the model VERIFIES a soft keyword match before dispatch.

When the keyword router fires a soft status/capability intent, the model
confirms the user is actually REQUESTING it (not chatting / quoting). If the
model judges conversation, the keyword match is suppressed and the turn falls
through to the normal path. Explicit imperative intents are not gated.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import main


class _FakeLLM:
    def __init__(self, canned: str):
        self.canned = canned

    def complete(self, system: str, user: str, **kw) -> str:
        return self.canned


def _fake_agent(canned: str):
    llm = _FakeLLM(canned)
    return SimpleNamespace(
        log=SimpleNamespace(log=lambda *a, **k: None),
        model_router=SimpleNamespace(for_role=lambda role: llm),
    )


def test_model_confirms_real_capability_request_dispatches(monkeypatch):
    dispatched = {}

    def _fake_dispatch(intent, agent, workspace, **kw):
        dispatched["kind"] = intent.kind
        return True

    monkeypatch.setattr(main, "_dispatch_operator_intent", _fake_dispatch)
    agent = _fake_agent('{"kind":"action","action":"capability_check","confidence":0.95,"reasoning":"asks"}')
    handled = main.handle_conversational_operator_input("что ты умеешь делать сейчас", agent, Path("."))
    assert handled is True
    assert dispatched.get("kind") == "capability_check"


def test_model_judges_conversation_suppresses_dispatch(monkeypatch):
    dispatched = {}
    monkeypatch.setattr(main, "_dispatch_operator_intent",
                        lambda *a, **k: dispatched.setdefault("called", True) or True)
    # A rambling message that merely contains the phrase -> model says conversation.
    agent = _fake_agent('{"kind":"conversation","action":null,"confidence":0.9,"reasoning":"just musing"}')
    handled = main.handle_conversational_operator_input(
        "починка виднелась мне во сне, я лишь написал предложение что ты умеешь делать, прикольно",
        agent, Path("."),
    )
    assert handled is False            # fell through to the normal path
    assert "called" not in dispatched  # dispatch was suppressed


def test_uncertain_model_preserves_deterministic_routing(monkeypatch):
    # Model output is garbage (or a mock stub) -> the bridge must NOT suppress;
    # deterministic routing is preserved (offline / degraded-model safety).
    dispatched = {}

    def _fake_dispatch(intent, agent, workspace, **kw):
        dispatched["kind"] = intent.kind
        return True

    monkeypatch.setattr(main, "_dispatch_operator_intent", _fake_dispatch)
    agent = _fake_agent("not json at all, just a stub")
    handled = main.handle_conversational_operator_input("Проверь проект", agent, Path("."))
    assert handled is True
    assert dispatched.get("kind") == "project_health"
