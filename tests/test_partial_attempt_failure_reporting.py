"""A denial inside a SUCCESSFUL attempt is downgraded to an ordinary failure.

WHO NEEDS THIS FILE. Anyone reasoning about what the completion arbiter knows.
`core/loop_attempt.py` breaks out of the loop at :441 when the attempt produced
any artifact — and that break happens BEFORE :448, where the attempt's triggers
are extended into `failure_history`. So a plan where one step succeeds and
another is denied carries its denial only as far as the end of that attempt.

WHAT SURVIVES ANYWAY, measured rather than assumed. The arbiter is not blinded:
`core/loop_attempt.py:408` sets `step.status = "failed"`, and
`core/completion_obligation.py:250` branches on `status == "failed" or
failure_codes`. The denied step is still marked, and an answer that names the
tool is still credited — three-pole probe, 2026-08-09: a silent answer gives
`silently_missing`, an answer naming `web_search` gives `failed_but_reported`.
The loss is masked for that distinction.

WHAT DOES NOT SURVIVE. `blocked_and_disclosed` — the state that says a policy
declined this, as opposed to it merely not working. That branch tests `tool in
denied`, and `denied` is built at `core/loop_run_tail.py:301` from
`failure_history` entries whose code is `policy_blocked`. On the success path
that list is empty, so the branch is unreachable and the denial reads as a
generic failure. Measured at the consumer with everything else held equal:
identical answer, identical step, identical status, and only the trigger
differing, gives `blocked_and_disclosed` versus `failed_but_reported`.

A TRAP RECORDED, because it nearly became a finding. The first probe reported
the successfully read file as `silently_missing`. The cause was the probe: it
hand-wrote the artifact label as `ok:1`, while `core/step_sanitizer.py:161`
builds `file:{path}`, and the arbiter looks for the file name inside the
artifact labels. With the real format the obligation is `satisfied`. A test
harness that invents a label tests its own invention.
"""
from __future__ import annotations

import pytest

from core.completion_obligation import evaluate_completion_obligations

_ANSWER_NAMING_THE_TOOL = (
    "Conclusion: web_search was blocked by policy, so this answer is partial."
)


class _DeniedStep:
    """A planned observing step the gate refused: planned, failed, no artifact."""

    def __init__(self) -> None:
        self.action_spec = {
            "type": "tool_call", "tool_name": "web_search", "arguments": {},
        }
        self.status = "failed"


def _tool_obligation(*, failure_codes: list[str], denied_tools: tuple[str, ...]) -> str:
    report = evaluate_completion_obligations(
        question="найди в интернете x",
        answer=_ANSWER_NAMING_THE_TOOL,
        plan_steps=[_DeniedStep()],
        artifacts={},
        chain_size=0,
        realtime_required=False,
        file_hint=None,
        failure_codes=failure_codes,
        denied_tools=denied_tools,
        contract=None,
    )
    states = [o.status for o in report.obligations if o.kind == "tool_execution"]
    assert len(states) == 1, f"expected exactly one tool obligation, got {states}"
    return states[0]


def test_the_arbiter_can_tell_a_refusal_from_a_breakdown() -> None:
    """The distinction exists and is worth keeping: it is what `denied` buys."""
    assert _tool_obligation(
        failure_codes=["policy_blocked"], denied_tools=("web_search",)
    ) == "blocked_and_disclosed"


def test_without_the_trigger_the_same_refusal_reads_as_a_breakdown() -> None:
    """Everything else held equal — same answer, same step, same status."""
    assert _tool_obligation(failure_codes=[], denied_tools=()) == "failed_but_reported"


def test_a_silent_answer_is_still_caught_either_way() -> None:
    """GUARD: the downgrade must not be confused with going unnoticed."""
    report = evaluate_completion_obligations(
        question="найди в интернете x",
        answer="Conclusion: here is what I found.",
        plan_steps=[_DeniedStep()],
        artifacts={},
        chain_size=0,
        realtime_required=False,
        file_hint=None,
        failure_codes=[],
        denied_tools=(),
        contract=None,
    )
    states = [o.status for o in report.obligations if o.kind == "tool_execution"]
    assert states == ["silently_missing"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN GAP, measured 2026-08-09 and banked rather than fixed: an "
        "attempt that produces any artifact breaks before its triggers reach "
        "failure_history, so a policy denial occurring in that attempt can "
        "never reach `blocked_and_disclosed`. When this XPASSes the loop "
        "started carrying the trigger — replace the marker with a plain "
        "assertion and record which mechanism closed it. "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
)
def test_a_refusal_is_classified_as_a_refusal_wherever_it_happens() -> None:
    """States the property, not a design: a denial should read as a denial.

    The inputs below are what `core/loop_run_tail.py` actually passes after a
    partially successful attempt — empty codes and empty denied set, because
    the break at `core/loop_attempt.py:441` came before the extend at :448.
    """
    assert _tool_obligation(
        failure_codes=[], denied_tools=()
    ) == "blocked_and_disclosed"
