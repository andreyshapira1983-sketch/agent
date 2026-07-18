"""The translator between plain human language and agent actions (Step 1).

Unlike the keyword router, this asks the MODEL to judge intent — but it is
GROUNDED: it may only choose an action that actually exists in the agent's real
capability list (kernel truth), and any malformed / low-confidence / invented
answer falls back to `conversation` (safe: just talk). These tests drive the
contract with a fake LLM, so no real model is called.
"""
from __future__ import annotations

from core.intent_understanding import IntentDecision, understand_intent


class _FakeLLM:
    def __init__(self, canned: str):
        self.canned = canned
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, **kw) -> str:
        self.calls.append((system, user))
        return self.canned


ACTIONS = ("capability_query", "project_health", "architecture_audit")


def test_clear_request_maps_to_grounded_action():
    llm = _FakeLLM('{"kind":"action","action":"capability_query","confidence":0.95,"reasoning":"asks what it can do"}')
    d = understand_intent("что ты умеешь делать сейчас", available_actions=ACTIONS, llm=llm)
    assert isinstance(d, IntentDecision)
    assert d.kind == "action" and d.action == "capability_query"
    # the real capability list was grounded into the prompt
    assert "capability_query" in llm.calls[0][0]


def test_conversational_mention_is_not_an_action():
    # The user only MENTIONED the phrase inside a reflection — model says conversation.
    llm = _FakeLLM('{"kind":"conversation","action":null,"confidence":0.9,"reasoning":"just musing, quotes the phrase"}')
    d = understand_intent(
        "починка виднелась мне во сне... я лишь написал предложение что ты умеешь делать, прикольно",
        available_actions=ACTIONS, llm=llm,
    )
    assert d.kind == "conversation" and d.action is None


def test_invented_action_is_grounded_out():
    # Model returns an action that does NOT exist in the real list -> reject -> conversation.
    llm = _FakeLLM('{"kind":"action","action":"launch_missiles","confidence":0.99,"reasoning":"x"}')
    d = understand_intent("do the thing", available_actions=ACTIONS, llm=llm)
    assert d.kind == "conversation" and d.action is None


def test_low_confidence_falls_back_to_conversation():
    llm = _FakeLLM('{"kind":"action","action":"project_health","confidence":0.2,"reasoning":"unsure"}')
    d = understand_intent("hmm", available_actions=ACTIONS, llm=llm)
    assert d.kind == "conversation"


def test_malformed_output_falls_back_safely():
    llm = _FakeLLM("I'm not going to answer in JSON, sorry.")
    d = understand_intent("anything", available_actions=ACTIONS, llm=llm)
    assert d.kind == "conversation" and d.action is None


def test_json_wrapped_in_fences_is_parsed():
    llm = _FakeLLM('```json\n{"kind":"action","action":"project_health","confidence":0.8,"reasoning":"status ask"}\n```')
    d = understand_intent("как проект", available_actions=ACTIONS, llm=llm)
    assert d.kind == "action" and d.action == "project_health"
