"""A clarification must not throw the question away — across the real boundary.

FIRST REPAIR, AND ITS LIVE REGRESSION. Holding the pending question on the
`AgentLoop` passed a test that reused one object and failed in production. The
reply arrived with `question_chars=467`, no `clarification_resumed`, a new
`session_id`, `turn_index=1`. The relevance repair shipped in the SAME runtime
worked in that run, so the code was current; what differed was the lifecycle.
The agent that asks and the agent that hears the answer are not the same object.

So the test that matters is the one below with TWO agents. The single-agent case
is kept because it is also real — but on its own it is exactly the test that
passed while production was broken.

WHY A DEDICATED RECORD. Surveyed before choosing: checkpoints carry
`original_user_question` but `CheckpointLoader.load` needs a `trace_id` that
`cli/resume.py` gets from the operator, and a fresh process holding a plain reply
has none; `user_profile` is who the operator is; `runtime_tasks` belongs to the
autonomous contour; persistent memory is knowledge and its writes are gated and
de-duplicated; dialogue history does not survive the process. Nothing had both
the right ownership and discovery without an id. See
`core/pending_clarification.py`.
"""
from __future__ import annotations

import json
import time
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


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _pending_path(workspace: Path) -> Path:
    return workspace / "data" / "pending_clarification.json"


def _fresh_agent(workspace: Path) -> tuple[AgentLoop, Path]:
    """A NEW AgentLoop, as a new process would build. Only the path is shared."""
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
        pending_clarification_path=_pending_path(workspace),
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
    agent, log_path = _fresh_agent(workspace)
    assert agent.run(_AMBIGUOUS), "the gate must return a clarification question"
    assert any(e.get("event") == "clarification_request" for e in _events(log_path))


def test_the_question_survives_the_process_that_asked(workspace: Path) -> None:
    agent, _log = _fresh_agent(workspace)
    agent.run(_AMBIGUOUS)
    assert _pending_path(workspace).exists(), (
        "the run that asked for clarification left nothing a later process can read"
    )
    held = json.loads(_pending_path(workspace).read_text(encoding="utf-8"))
    assert held["question"] == _AMBIGUOUS


def test_A_NEW_AGENT_answers_the_reply_together_with_the_original(
    workspace: Path,
) -> None:
    """The production failure, reproduced across the boundary that caused it."""
    asker, _first = _fresh_agent(workspace)
    asker.run(_AMBIGUOUS)

    answerer, second_log = _fresh_agent(workspace)   # a different object entirely
    answerer.run(_REPLY)

    events = _events(second_log)
    asked = _asked_question(events)
    assert _AMBIGUOUS in asked, (
        "a new agent treated the REPLY as the entire question — this is the live "
        f"regression of 2026-08-09. observe saw: {asked!r}"
    )
    assert _REPLY in asked
    assert any(e.get("event") == "clarification_resumed" for e in events)


def test_the_same_agent_continuation_also_works(workspace: Path) -> None:
    agent, _first = _fresh_agent(workspace)
    agent.run(_AMBIGUOUS)
    agent.log = TraceLogger(trace_id=(tid := new_trace_id()),
                            log_dir=workspace / "logs", verbose=False)
    agent.run(_REPLY)
    assert _AMBIGUOUS in _asked_question(_events(workspace / "logs" / f"{tid}.jsonl"))


def test_the_pending_question_is_taken_exactly_once(workspace: Path) -> None:
    """A third message must not inherit a task the second already answered."""
    asker, _a = _fresh_agent(workspace)
    asker.run(_AMBIGUOUS)
    answerer, _b = _fresh_agent(workspace)
    answerer.run(_REPLY)

    third, third_log = _fresh_agent(workspace)
    third.run("совсем другой вопрос про doc.txt")
    assert not _pending_path(workspace).exists()
    assert _asked_question(_events(third_log)) == "совсем другой вопрос про doc.txt"


def test_a_stale_record_is_never_inherited(workspace: Path) -> None:
    """An abandoned clarification must not attach itself to tomorrow's request."""
    asker, _a = _fresh_agent(workspace)
    asker.run(_AMBIGUOUS)
    path = _pending_path(workspace)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["written_at"] = time.time() - (31 * 60)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    later, later_log = _fresh_agent(workspace)
    later.run("что в doc.txt")
    assert _asked_question(_events(later_log)) == "что в doc.txt"


def test_a_resumed_run_is_never_re_gated(workspace: Path) -> None:
    """The trigger is in the original text, so re-asking could only ask forever."""
    asker, _a = _fresh_agent(workspace)
    asker.run(_AMBIGUOUS)
    answerer, second_log = _fresh_agent(workspace)
    answerer.run(_REPLY)
    assert not any(e.get("event") == "clarification_request"
                   for e in _events(second_log))
    assert not _pending_path(workspace).exists(), (
        "a re-gated resumption would park the composed question and loop"
    )


def test_an_unclarified_run_parks_nothing(workspace: Path) -> None:
    """GUARD: the assertions above must not be satisfied by always parking."""
    agent, log_path = _fresh_agent(workspace)
    agent.run("что в doc.txt")
    assert not _pending_path(workspace).exists()
    assert _asked_question(_events(log_path)) == "что в doc.txt"
