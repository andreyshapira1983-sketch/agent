"""Why is the plan empty? The attempt loop asks that question once, of six.

WHO NEEDS THIS FILE. Anyone reading `core/loop_attempt.py:441` and taking its
success condition at face value. `if (not st.plan.steps and not
plan_parse_failed) or attempt_artifacts: break` treats "no steps" as an
intentional general-knowledge plan, and `plan_parse_failed` is the only other
provenance it distinguishes. `core/planner.py:_validate_steps` can empty a plan
in at least five further ways: a step that is not an object, a step with no tool
name, an unregistered tool, a `(tool, args)` pair on the forbidden list, and a
step the sanitiser rejects. Of those, only the unregistered tool leaves a
structured trace (`plan_tool_drop`); the rest leave free-text warnings.

MEASURED, in the SHIPPED configuration, with nothing mutated (2026-08-09). Four
failure types both allow a retry and forbid repeating the action —
approval_deny, approval_abort, policy_blocked and claim_refuted — so this is an
ordinary path, not a corner:

  attempt 1 plans one step -> the policy gate blocks it -> policy_blocked, which
  is budget 2, so the policy says continue and puts (tool, args) on the
  forbidden list -> attempt 2's plan comes back EMPTY, filtered by that same
  list -> the loop breaks on the SUCCESS branch -> `replan_exhausted` is never
  set -> the gate at `core/loop_synthesis.py:637` hands the synthesizer None ->
  the answer is composed from general knowledge.

And the obligation arbiter, which exists precisely to catch a silently missing
observation, receives `plan_steps` from that last empty plan and reports "no
wired source created an obligation". The run HELD the denial — `failure_history`
carries the policy_blocked trigger and `core/loop_run_tail.py:301` reads it into
`denied_tools` — and had nothing left to attach it to. The mechanism that
emptied the plan is the same one that removed the obligation the denial would
have marked.

WHAT IS ASSERTED HERE. The mechanism, which is not in dispute and must not
regress: the step really is stopped, the denial really is recorded, and the
retry really does come back empty. Those pass today.

The distinguishability claim is marked `xfail(strict=True)` on purpose. It does
not prescribe a design — it says only that a run denied its only tool should
differ from a run that needed no tool somewhere other than the journal. Today it
does not. Any repair makes the marker XPASS, and strict turns that into a
failure, which is the signal to come back and turn it into a plain assertion.
Production is read-only for the mapping program that found this, so the gap is
banked and announced rather than closed here.
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

_ANSWER = (
    "Conclusion: ok. [general-knowledge]\nFacts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)

#: A step naming a tool no registry holds. The policy gate refuses it, which is
#: `policy_blocked` — budget 2 and `requires_different_action=True`, the exact
#: combination that produces the retry-then-empty sequence.
_GHOST_STEP = {
    "tool": "ghost_tool", "arguments": {}, "label": "s:1",
    "expected_outcome": "never produced",
}


def _run(workspace: Path, sources: list[dict]) -> tuple[str, list[dict], FakeLLM]:
    registry = ToolRegistry()          # deliberately empty: nothing is registered
    llm = FakeLLM(responses=[_ANSWER] * 4)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        planner=FakePlanner(sources=sources),
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        memory=None,
        max_replan_attempts=3,         # the shipped default; at 1 the global cap
    )                                  # pre-empts the per-type rule this rides on
    answer = agent.run("расскажи про предмет")
    events = [
        json.loads(line)
        for line in (workspace / "logs" / f"{trace_id}.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return answer, events, llm


def _plans(events: list[dict]) -> list[dict]:
    return [e["payload"] for e in events if e.get("event") == "plan"]


def _non_journal_view(answer: str, events: list[dict], llm: FakeLLM) -> dict:
    """Everything a consumer OTHER than the journal reader can see."""
    prompts = " || ".join(call["user"] for call in llm.calls)
    arbiter = next(
        (e["payload"] for e in events if e.get("event") == "completion_obligation"),
        {},
    )
    return {
        "failure_context_reached_synthesis": "<failure_context>" in prompts,
        "obligation_count": len(arbiter.get("obligations", [])),
        "answer_mentions_a_block": any(
            word in answer.casefold()
            for word in ("block", "polic", "denied", "запрещ", "не удалось")
        ),
        "replan_exhausted": any(e.get("event") == "replan_exhausted" for e in events),
    }


def test_the_denied_step_is_stopped_and_the_retry_comes_back_empty(
    tmp_path: Path,
) -> None:
    """The mechanism, asserted so it cannot regress while the gap is open."""
    _answer, events, _llm = _run(tmp_path, [_GHOST_STEP])

    names = [e.get("event") for e in events]
    assert names.count("policy") == 1, "precondition: the gate must have ruled once"
    assert "replan_attempt" in names, (
        "policy_blocked has budget 2, so the first denial must not end the loop"
    )

    plans = _plans(events)
    assert len(plans) == 2, f"expected two attempts, saw {len(plans)}"
    assert len(plans[0]["steps"]) == 1, "attempt 1 must really have planned the step"
    assert plans[1]["steps"] == [], (
        "attempt 2 must come back empty — the forbidden list removes the only "
        "source, and that emptying is what the rest of this file is about"
    )


def test_no_artifact_is_invented_for_the_denied_step(tmp_path: Path) -> None:
    """GUARD: a run that answers anyway must at least not claim tool evidence."""
    _answer, events, _llm = _run(tmp_path, [_GHOST_STEP])
    collected = [e for e in events if e.get("event") == "evidence_collected"]
    assert collected, "precondition: the evidence phase must have run"
    assert collected[-1]["payload"]["count"] == 0, (
        "the blocked step produced no artifact, so the chain must be empty"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN GAP, measured 2026-08-09 and banked rather than fixed: a run "
        "denied its only tool is indistinguishable from a run that needed no "
        "tool at every consumer except the journal. When this XPASSes, the "
        "behaviour changed — replace the marker with a plain assertion and "
        "record which mechanism closed it."
    ),
)
def test_a_denied_run_differs_from_a_no_tool_run_somewhere_outside_the_journal(
    tmp_path: Path,
) -> None:
    denied = _non_journal_view(*_run(tmp_path / "denied", [_GHOST_STEP]))
    no_tool = _non_journal_view(*_run(tmp_path / "no_tool", []))
    assert denied != no_tool, (
        "both runs present the same face to synthesis, to the obligation "
        f"arbiter and to the user: {denied}"
    )
