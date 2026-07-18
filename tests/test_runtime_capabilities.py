"""Runtime capability introspection (#2).

Live-run defect: "что ты умеешь делать сейчас" did NOT match the
`capability_check` intent (the matcher had "что ты можешь делать" but not the
verb "умеешь"), so it fell through to `general_question` and the planner ran a
web_search about ChatGPT instead of describing THIS running agent. And even the
capability handler reported operator status, not the live tool registry.

This covers:
  1. routing — "умеешь" / "what can you do" phrasings route to capability_check;
  2. content — the runtime facts helper lists the agent's live registered tools.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.operator_intent import route_operator_intent


# --- 1. routing -----------------------------------------------------------

def test_umeesh_phrasings_route_to_capability_check():
    for text in (
        "что ты умеешь делать сейчас",
        "что ты умеешь",
        "что умеешь делать",
        "what can you do",
        "what are you capable of",
    ):
        intent = route_operator_intent(text)
        assert intent is not None and intent.kind == "capability_check", (
            f"{text!r} did not route to capability_check (got {intent})"
        )


def test_capability_router_stays_narrow():
    # A plain unrelated request must NOT be swallowed by the capability route.
    assert route_operator_intent("умеет ли пользователь программировать на Rust") is None


# --- 2. content: live runtime facts --------------------------------------

def test_runtime_capability_facts_lists_live_tools():
    from main import _runtime_capability_facts

    fake_agent = SimpleNamespace(
        registry=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(name="file_read"),
                SimpleNamespace(name="web_search"),
                SimpleNamespace(name="shell_exec"),
            ]
        ),
        memory=object(),          # present -> "on"
        persistent_store=None,    # absent  -> "off"
    )
    facts = _runtime_capability_facts(fake_agent)
    assert facts["registered_tools"] == ["file_read", "shell_exec", "web_search"]
    assert facts["tool_count"] == 3
    assert facts["memory"] == "on"
    assert facts["persistent_memory"] == "off"
