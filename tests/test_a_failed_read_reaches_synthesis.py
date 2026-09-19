"""A step that failed must reach the synthesiser even when the turn succeeds.

Measured live on 2026-08-14, four runs. Rule 11 planned `file_read README.md`,
the file had been deleted eight days earlier, the tool returned
FileNotFoundError with the absolute path — and the agent, asked whether the
file exists, could only answer "cannot be determined". It was not confused: the
loop dropped the failed step (`core/loop_attempt.py:406`, `outcome is None ->
continue`) and `<failure_context>` was gated on `st.replan_exhausted`, which is
False whenever any step produced an artifact. The tool had the answer and
nothing carried it to synthesis.

`<failure_context>` is not an `<evidence>` block, so the failure gains a voice
without gaining citation power — putting it among the artifacts would make the
model cite a source absent from the provenance chain, which the verifier books
as a fabricated citation.

The plan here mixes one good step with one bad one on purpose: the attempt
SUCCEEDS (the loop's success condition is "any artifact survived"), replan is
never exhausted, and that is precisely the case the old gate hid.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_ANSWER = (
    "Analysis: probe.\nFindings: probe [file:present.txt]\n"
    "Conclusion: probe\n"
    "Sources:\n1. file:present.txt - present.txt\n"
    "Confidence: medium\nUnverified: nothing\n"
)

MISSING = "absent_7f91c.txt"


def _run(workspace: Path) -> tuple[str, list[dict], FakeLLM]:
    (workspace / "present.txt").write_text("alpha=1\n", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    llm = FakeLLM(responses=[_ANSWER] * 4)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        planner=FakePlanner(sources=[
            {"tool": "file_read", "arguments": {"path": "present.txt"},
             "label": "s:1", "expected_outcome": "content"},
            {"tool": "file_read", "arguments": {"path": MISSING},
             "label": "s:2", "expected_outcome": "content"},
        ]),
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        memory=None,
        max_replan_attempts=3,
    )
    answer = agent.run("что лежит в файлах")
    events = [
        json.loads(line)
        for line in (workspace / "logs" / f"{trace_id}.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return answer, events, llm


def test_the_failed_read_is_in_the_synthesis_prompt(workspace: Path):
    """The fact the tool established must be visible where the answer is written."""
    _answer, events, llm = _run(workspace)

    # The turn really did succeed on the other step — otherwise this test would
    # be measuring replan exhaustion, which was never the broken case.
    exhausted = [
        e for e in events
        if e.get("event") == "error"
        and "replan" in str(e.get("payload", {}).get("code", ""))
    ]
    assert not exhausted, f"replan was exhausted; wrong scenario: {exhausted}"

    prompts = " || ".join(call["user"] for call in llm.calls)
    assert "<failure_context>" in prompts, (
        "a step failed and the synthesiser was told nothing; the agent cannot "
        "report what it could not read"
    )
    assert MISSING in prompts, (
        "the failure block reached the prompt without naming the path that "
        f"failed: {prompts[-600:]}"
    )
    assert "file_not_found" in prompts, (
        "the failure reached the prompt without its code, so the synthesiser "
        "cannot tell an absent file from a broken tool"
    )


def test_the_failure_is_not_offered_as_a_citable_source(workspace: Path):
    """Context, not evidence.

    If the failure were wrapped as `<evidence source=...>` the model would cite
    it, the citation would resolve to nothing in the provenance chain, and the
    verifier would book a fabricated citation — trading a silent gap for a
    louder lie.
    """
    _answer, _events, llm = _run(workspace)
    prompts = " || ".join(call["user"] for call in llm.calls)

    assert f'<evidence source="file:{MISSING}"' not in prompts
    assert f"[file:{MISSING}]" not in prompts.split("<failure_context>")[0], (
        "the missing file appears on the citable side of the prompt"
    )


def test_a_clean_turn_carries_no_failure_block(workspace: Path):
    """The anti-vacuity pole: no failures, no block, no tokens spent."""
    (workspace / "present.txt").write_text("alpha=1\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    llm = FakeLLM(responses=[_ANSWER] * 4)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        planner=FakePlanner(sources=[
            {"tool": "file_read", "arguments": {"path": "present.txt"},
             "label": "s:1", "expected_outcome": "content"},
        ]),
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        memory=None,
        max_replan_attempts=3,
    )
    agent.run("что лежит в файле")

    prompts = " || ".join(call["user"] for call in llm.calls)
    assert "<failure_context>" not in prompts
