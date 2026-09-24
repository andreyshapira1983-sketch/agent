"""Four sensors guarded inside one method; two report their own death, two do not.

WHO NEEDS THIS FILE. Anyone who reads `core/loop_attempt.py:_run_attempt_loop`
and takes its four `except` blocks for one policy. They are two policies:

    :335  check_reasoning_actions   ->  except Exception: pass
    :359  extract_from_plan         ->  except Exception: pass
    :470  termination_guard         ->  self._sensor_failed("stagnation_shadow")
    :627  budget pause checkpoint   ->  self._sensor_failed("budget_exhaustion_log")

`tests/test_journal_silence_ratchet.py` already counts journal-silent handlers
across the loop layer and holds the number exactly. It answers "how many", which
is the right question for a budget. It does not ask what any particular one
hides, and these two hide something.

MEASURED, 2026-08-09, by making each guarded call raise and reading the journal:

    extract_from_plan broken    -> `assumptions_registered` 1 -> 0, and nothing
                                   else changes. That is the arc C01 certified
                                   as assumption_seeding, switched off in silence.
    check_reasoning_actions
                     broken     -> `reasoning_action_mismatch` 1 -> 0 AND
                                   `_defect_signals` ['reasoning_action_mismatch']
                                   -> []. The second half is durable: the list is
                                   banked with the episode, so the agent's own
                                   memory of a repeated fault loses an entry.

In both cases `sensor_failed` stays at zero. The run looks exactly like a run
where the sensor had nothing to report — which is the failure mode this project
already named once, in the guard that says a silent handler must at least carry
a comment. Both of these carry comments. The comment helps a reader; it does
nothing for an operator reading logs at three in the morning.

THE CONTRAST IS IN THE SAME METHOD, and it is asserted below rather than
described: break the termination guard on the failure path and `sensor_failed`
appears, carrying the sensor's name.

TWO OF MY OWN MISREADINGS ARE RECORDED IN THE HELPERS, because each cost a
"nothing happened" that looked like a result: the loop REPLACES
`self._termination_guard` on every `run()`, so patching the instance is undone
before the sensor is consulted; and the event is spelled `sensor_failed`, not
`sensor_failure`, so a counter watching the wrong name reports zero for a
handler that reported perfectly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import loop_attempt, termination_guard
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_ANSWER = (
    "Conclusion: ok. [file_read:doc.txt]\n"
    "Facts:\n- 3 lines [file_read:doc.txt]\n"
    "Sources:\n1. file_read - doc.txt\n"
    "Confidence: medium\nUnverified: nothing\n"
)
_READ = {"tool": "file_read", "arguments": {"path": "doc.txt"},
         "label": "file:doc.txt", "expected_outcome": "the lines"}
#: A tool no registry holds: the gate refuses it and the loop takes its FAILURE
#: path, which is the only path where the stagnation sensor is consulted.
_GHOST = {"tool": "ghost_tool", "arguments": {}, "label": "web:x",
          "expected_outcome": "never"}


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("boom")


def _run(workspace: Path, sources: list[dict]) -> list[dict]:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "doc.txt").write_text("a\nb\nc\n", encoding="utf-8")
    registry = ToolRegistry()
    if any(s["tool"] == "file_read" for s in sources):
        registry.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        planner=FakePlanner(sources=sources),
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 4),
        logger=logger,
        memory=None,
        max_replan_attempts=3,
    )
    agent.run("что в doc.txt")
    return [
        json.loads(line)
        for line in (workspace / "logs" / f"{trace_id}.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _events_named(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e.get("event") == name]


def test_the_two_sensors_speak_when_they_are_healthy(tmp_path: Path) -> None:
    """Precondition for everything below: both signals exist in a normal run."""
    events = _run(tmp_path / "healthy", [_READ])
    assert _events_named(events, "assumptions_registered"), (
        "the assumption extractor must produce a signal, or breaking it proves nothing"
    )
    assert _events_named(events, "reasoning_action_mismatch"), (
        "the reasoning/action check must produce a signal for this plan"
    )


def test_a_dead_stagnation_sensor_names_itself_in_the_journal(tmp_path: Path) -> None:
    """The contrast, in the same method: this is what reporting looks like.

    The guard is REPLACED on every `run()`, so the class is patched rather than
    the instance — patching the instance is silently undone before the sensor
    is ever consulted, and the test then measures nothing.
    """
    original = termination_guard.TerminationGuard.observe_attempt
    termination_guard.TerminationGuard.observe_attempt = _boom
    try:
        events = _run(tmp_path / "stagnation", [_GHOST])
    finally:
        termination_guard.TerminationGuard.observe_attempt = original

    failures = _events_named(events, "sensor_failed")
    assert failures, (
        "a sensor that raised on the failure path must leave a journal entry; "
        "note the event is spelled `sensor_failed`, not `sensor_failure`"
    )
    assert failures[0]["payload"]["sensor"] == "stagnation_shadow"
    assert failures[0]["payload"]["error_type"] == "RuntimeError"


def test_a_broken_assumption_extractor_loses_a_certified_arc(tmp_path: Path) -> None:
    """Characterisation of the loss itself, which is not in dispute."""
    original = loop_attempt.extract_from_plan
    loop_attempt.extract_from_plan = _boom
    try:
        events = _run(tmp_path / "assumptions", [_READ])
    finally:
        loop_attempt.extract_from_plan = original
    assert not _events_named(events, "assumptions_registered"), (
        "precondition: the extractor really was disabled"
    )


def test_a_broken_reasoning_check_loses_a_durable_defect_signal(tmp_path: Path) -> None:
    original = loop_attempt.check_reasoning_actions
    loop_attempt.check_reasoning_actions = _boom
    try:
        events = _run(tmp_path / "reasoning", [_READ])
    finally:
        loop_attempt.check_reasoning_actions = original
    assert not _events_named(events, "reasoning_action_mismatch"), (
        "precondition: the check really was disabled"
    )


@pytest.mark.parametrize(
    "attribute,sources",
    [
        pytest.param("__stagnation__", [_GHOST], id="termination_guard"),
        pytest.param("extract_from_plan", [_READ], id="assumption_extractor"),
        pytest.param("check_reasoning_actions", [_READ], id="reasoning_action_check"),
    ],
)
def test_a_sensor_that_dies_inside_the_attempt_loop_is_reported(
    tmp_path: Path, attribute: str, sources: list[dict]
) -> None:
    """One property, stated uniformly; the marks record who does not meet it.

    This prescribes no design — only that a sensor failing inside the attempt
    loop should be visible somewhere other than by its output going missing.
    """
    if attribute == "__stagnation__":
        original = termination_guard.TerminationGuard.observe_attempt
        termination_guard.TerminationGuard.observe_attempt = _boom
        try:
            events = _run(tmp_path / attribute, sources)
        finally:
            termination_guard.TerminationGuard.observe_attempt = original
    else:
        original = getattr(loop_attempt, attribute)
        setattr(loop_attempt, attribute, _boom)
        try:
            events = _run(tmp_path / attribute, sources)
        finally:
            setattr(loop_attempt, attribute, original)

    assert _events_named(events, "sensor_failed"), (
        f"the {attribute} sensor raised and the journal says nothing; the run "
        "is indistinguishable from one where that sensor had nothing to report"
    )
