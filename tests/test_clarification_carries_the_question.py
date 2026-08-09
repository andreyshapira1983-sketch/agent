"""A clarification must not throw the question away.

MEASURED IN PRODUCTION, 2026-08-09, twice in a row. The operator asked a
2838-character post-mortem question. `_clarification_gate` fired, the run
returned the clarification and ended. The operator answered "Perform the
investigation exactly as requested…" — 467 characters — and that reply became
the WHOLE of `user_question` for the next run:

    observe.content   = the reply alone
    interpret         = "Answer the question: Perform the investigation…"
    adaptive_route    = question_chars=467
    planner reasoning = "Rule 11a (doctrine question)"

The agent then investigated the self-repair doctrine — a different subject
entirely — and shipped an answer that passed verification 14/16, because every
claim in it was correctly cited to sources chosen for the wrong question.

Nothing could recover the original: both banked runs carry `turn_index=1` under
DIFFERENT session ids, so the dialogue history the planner receives was empty.

The gate is right to ask. What is wrong is that asking discards what was asked
about. These tests pin the repair: the question survives the clarification and
is re-attached when the operator answers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

#: Trips `check_clarification` — the shape the operator's real question had.
_AMBIGUOUS = "Do not fix or modify anything"
_REPLY = "Perform the investigation exactly as requested."
_ANSWER = (
    "Conclusion: ok. [general-knowledge]\n"
    "Facts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _agent(workspace: Path) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 4),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[]),
        memory=None,
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def _events(log_path: Path) -> list[dict]:
    return [json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _asked_question(events: list[dict]) -> str:
    """What the run actually treated as the question, from its own journal."""
    for event in events:
        if event.get("event") == "observe":
            return str(event["payload"]["content"]["question"])
    return ""


def test_the_gate_still_asks(workspace: Path) -> None:
    """PRECONDITION: without this the rest would pass on a gate that never fires."""
    agent, log_path = _agent(workspace)
    answer = agent.run(_AMBIGUOUS)
    assert answer, "the gate must return a clarification question"
    assert any(e.get("event") == "clarification_request" for e in _events(log_path))


def test_the_question_survives_the_clarification(workspace: Path) -> None:
    agent, _first = _agent(workspace)
    agent.run(_AMBIGUOUS)

    pending = getattr(agent, "pending_clarification_question", None)
    assert pending == _AMBIGUOUS, (
        "the run that asked for clarification discarded what it was asked about; "
        f"pending={pending!r}"
    )


def test_the_reply_is_answered_together_with_the_original(workspace: Path) -> None:
    """The failure this file exists for: the reply must not become the whole task."""
    agent, _first = _agent(workspace)
    agent.run(_AMBIGUOUS)

    trace_id = new_trace_id()
    agent.log = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs",
                            verbose=False)
    agent.run(_REPLY)
    second = _events(workspace / "logs" / f"{trace_id}.jsonl")

    asked = _asked_question(second)
    assert _AMBIGUOUS in asked, (
        "the second run treated the REPLY as the entire question — this is the "
        f"production failure, reproduced. observe saw: {asked!r}"
    )
    assert _REPLY in asked, "the reply must still be part of the question"


def test_the_pending_question_is_cleared_after_it_is_used(workspace: Path) -> None:
    """A question that outlived its answer would contaminate the NEXT request."""
    agent, _first = _agent(workspace)
    agent.run(_AMBIGUOUS)
    agent.run(_REPLY)
    assert not getattr(agent, "pending_clarification_question", None)


def test_an_unclarified_run_carries_nothing_forward(workspace: Path) -> None:
    """GUARD: the assertions above must not be satisfied by always prepending."""
    agent, log_path = _agent(workspace)
    agent.run("что в doc.txt")
    assert not getattr(agent, "pending_clarification_question", None)
    assert _asked_question(_events(log_path)) == "что в doc.txt"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path
