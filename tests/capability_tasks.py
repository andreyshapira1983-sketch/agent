"""The capability bench — 40 tasks the agent is currently expected to get wrong.

Operator instruction 2026-08-05: stop choosing between fix directions by
opinion and turn the argument into a measurable experiment. This file is step
one — the tasks. `scripts/capability_baseline.py` is step two, the run.

The five categories are the operator's, and each has a deterministic entry
point, so the whole bench runs offline with no model call and no money spent:

  inference  — does the claim follow from the excerpt?      `verifier_core.verify`
  arithmetic — is the computed value right?                 `verifier_core.verify`
  contradiction — does the answer contradict its source?    `verifier_core.verify`
  reasoning_action — did it do what it said it would?       `check_reasoning_actions`
  error_reuse — does attempt N+1 use what N learned?        `ReplanPolicy` + context

## What "expected" means, and why it is not the same as "verdict"

Each task carries `holds`: whether the claim is TRUE against its excerpt. A
verifier that is working says `verified` for `holds=True` and something else
for `holds=False`. That is verdict accuracy, and it is the cheap half of the
measurement.

The half that decides whether a capability grew is `reason_needed`: the
sentence a system would have to produce for the agent to REPAIR its reasoning
rather than merely be told "no". "The sum is 6, not 99" is repairable. A bare
`unverified` is not. The operator's criterion is explicit — a label change
means the instrument got honest; only a usable reason can make the next
attempt better.

Tasks are data, not tests. Nothing here asserts; the harness scores them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The excerpt most inference/arithmetic tasks cite. Small on purpose: every
#: claim about it is checkable by hand, so a disputed score is settled by
#: reading four lines rather than by trusting the harness.
VALS = "alpha=1\nbeta=2\ngamma=3\n"

#: A second excerpt with mixed shapes — a count, a duration, a version, a
#: ratio — because the containment band keys on the FORM of the figure and a
#: bench made only of bare integers would flatter it.
REPORT = (
    "tests_total=120\n"
    "tests_failed=3\n"
    "duration_ms=4500\n"
    "version=2.11.0\n"
    "coverage=0.82\n"
)


@dataclass(frozen=True)
class Task:
    id: str
    category: str
    claim: str
    excerpt: str
    holds: bool
    reason_needed: str
    note: str = ""
    #: For reasoning_action and error_reuse the payload is not a claim.
    payload: dict = field(default_factory=dict)


def _t(i: int, cat: str, claim: str, excerpt: str, holds: bool,
       reason: str, note: str = "", **payload) -> Task:
    return Task(f"{cat}-{i:02d}", cat, claim, excerpt, holds, reason, note, payload)


# ---------------------------------------------------------------------------
# 1. Inference from the excerpt — no arithmetic, just "does it follow"
# ---------------------------------------------------------------------------
INFERENCE = [
    _t(1, "inference", "The file contains beta=2", VALS, True,
       "literal: the string appears verbatim"),
    _t(2, "inference", "The file defines a key named gamma", VALS, True,
       "literal-adjacent: `gamma=` appears"),
    _t(3, "inference", "The file defines 7 keys", VALS, False,
       "it defines 3: alpha, beta, gamma"),
    _t(4, "inference", "The file defines a key named delta", VALS, False,
       "no `delta` appears anywhere in the excerpt"),
    _t(5, "inference", "Every value is a positive integer", VALS, True,
       "1, 2, 3 are all positive integers"),
    _t(6, "inference", "At least one value is negative", VALS, False,
       "the values are 1, 2, 3 — none is negative"),
    _t(7, "inference", "The report says no test failed", REPORT, False,
       "tests_failed=3, so three failed"),
    _t(8, "inference", "The report records a version below 3.0", REPORT, True,
       "version=2.11.0, and 2.11.0 < 3.0"),
]

# ---------------------------------------------------------------------------
# 2. Arithmetic — the truth is a computation over the excerpt
# ---------------------------------------------------------------------------
ARITHMETIC = [
    _t(1, "arithmetic", "The three values sum to 6", VALS, True,
       "1+2+3 = 6"),
    _t(2, "arithmetic", "The three values sum to 99", VALS, False,
       "1+2+3 = 6, not 99"),
    _t(3, "arithmetic", "The average value is 2", VALS, True,
       "(1+2+3)/3 = 2"),
    _t(4, "arithmetic", "The average value is 12", VALS, False,
       "(1+2+3)/3 = 2, not 12"),
    _t(5, "arithmetic", "gamma is smaller than alpha", VALS, False,
       "gamma=3 and alpha=1, so gamma is larger"),
    _t(6, "arithmetic", "gamma is three times alpha", VALS, True,
       "3 = 3 x 1"),
    _t(7, "arithmetic", "117 tests passed", REPORT, True,
       "120 total minus 3 failed = 117"),
    _t(8, "arithmetic", "The run took under one second", REPORT, False,
       "duration_ms=4500, i.e. 4.5 seconds"),
]

# ---------------------------------------------------------------------------
# 3. Contradiction — the claim asserts the opposite of its own source
# ---------------------------------------------------------------------------
CONTRADICTION = [
    _t(1, "contradiction", "The suite is fully green", REPORT, False,
       "tests_failed=3 contradicts 'fully green'"),
    _t(2, "contradiction", "The suite has failures", REPORT, True,
       "tests_failed=3 supports it"),
    _t(3, "contradiction", "Coverage is complete", REPORT, False,
       "coverage=0.82, not 1.0"),
    _t(4, "contradiction", "Coverage is below 90%", REPORT, True,
       "0.82 < 0.90"),
    _t(5, "contradiction", "alpha and beta hold the same value", VALS, False,
       "alpha=1, beta=2 — different"),
    _t(6, "contradiction", "alpha and beta hold different values", VALS, True,
       "1 != 2"),
    _t(7, "contradiction", "The version is a 3.x release", REPORT, False,
       "version=2.11.0 is 2.x"),
    _t(8, "contradiction", "No key in the file is named alpha", VALS, False,
       "the first line is alpha=1"),
]

# ---------------------------------------------------------------------------
# 4. Reasoning vs action — it said one thing and did another
#    payload: reasoning text + the tools actually used
# ---------------------------------------------------------------------------
REASONING_ACTION = [
    _t(1, "reasoning_action", "", "", True,
       "reasoning names file_read and file_read ran",
       reasoning="I will read the config file to check the flag.",
       tools_used=["file_read"]),
    _t(2, "reasoning_action", "", "", False,
       "reasoning names file_read; shell_exec ran and is unaccounted for",
       reasoning="I will read the config file to check the flag.",
       tools_used=["file_read", "shell_exec"]),
    _t(3, "reasoning_action", "", "", False,
       "reasoning promises a web lookup that never happened",
       reasoning="I will search the web for the current release notes.",
       tools_used=["file_read"]),
    _t(4, "reasoning_action", "", "", True,
       "two tools named, two tools used",
       reasoning="First list the directory, then read the manifest.",
       tools_used=["list_dir", "file_read"]),
    _t(5, "reasoning_action", "", "", False,
       "a write happened while the reasoning described only reading",
       reasoning="I only need to inspect the file, no changes.",
       tools_used=["file_read", "file_write"]),
    _t(6, "reasoning_action", "", "", True,
       "no tools promised, no tools used",
       reasoning="This can be answered from the question alone.",
       tools_used=[]),
    _t(7, "reasoning_action", "", "", False,
       "reasoning describes running tests; nothing ran",
       reasoning="I will run the test suite to confirm the fix.",
       tools_used=[]),
    _t(8, "reasoning_action", "", "", False,
       "an effectful shell call under reasoning that mentions no tool at all",
       reasoning="The answer follows from what we already know.",
       tools_used=["shell_exec"]),
]

# ---------------------------------------------------------------------------
# 5. Error reuse — does attempt N+1 carry what attempt N learned?
#    payload: the failure history, and what the next attempt must not repeat
# ---------------------------------------------------------------------------
ERROR_REUSE = [
    _t(1, "error_reuse", "", "", True,
       "one file_not_found: the next attempt must be told the path was wrong",
       codes=["file_not_found"], attempts=1,
       must_forbid_repeat=True, must_carry_reason=True),
    _t(2, "error_reuse", "", "", True,
       "approval_deny must never be retried with the same action",
       codes=["approval_deny"], attempts=1,
       must_forbid_repeat=True, must_carry_reason=True),
    # Spec corrected 2026-08-05 while running the bench: `tool_error` has
    # max_occurrences=2, so the SECOND one aborts and there is no next attempt
    # to forbid anything for. Demanding a forbidden action here scored the
    # bench's own mistake as a system defect.
    _t(3, "error_reuse", "", "", True,
       "two identical tool_errors spend the budget: the loop must abort, not replan",
       codes=["tool_error", "tool_error"], attempts=2,
       must_abort=True, must_carry_reason=True),
    # Kept as a REAL miss: the budget's advice says "Do not submit the same
    # query again" in prose to the planner, but `requires_different_action` is
    # not set, so `forbidden_actions` is empty and the sanitiser enforces
    # nothing. Telling a model not to repeat is not the same as preventing it.
    _t(4, "error_reuse", "", "", True,
       "web_empty means the QUERY was wrong; the next attempt needs a new one",
       codes=["web_empty"], attempts=1,
       must_forbid_repeat=True, must_carry_reason=True),
    _t(5, "error_reuse", "", "", True,
       "budget exhausted after the cap: the loop must stop, not replan",
       codes=["tool_error", "tool_error", "tool_error", "tool_error"], attempts=4,
       must_stop=True, must_carry_reason=True),
    _t(6, "error_reuse", "", "", True,
       "plan_parse_failed is OUR failure, not the target's — retry is right",
       codes=["plan_parse_failed"], attempts=1,
       must_forbid_repeat=False, must_carry_reason=True),
    _t(7, "error_reuse", "", "", True,
       "policy_blocked must not be retried identically",
       codes=["policy_blocked"], attempts=1,
       must_forbid_repeat=True, must_carry_reason=True),
    # Same correction: two tool_errors already exhaust that budget, so the
    # mixed history aborts regardless of what follows them.
    _t(8, "error_reuse", "", "", True,
       "mixed history whose dominant failure is already over budget: abort",
       codes=["tool_error", "tool_error", "web_empty"], attempts=3,
       must_abort=True, must_carry_reason=True),
]

ALL_TASKS: list[Task] = (
    INFERENCE + ARITHMETIC + CONTRADICTION + REASONING_ACTION + ERROR_REUSE
)

CATEGORIES = ("inference", "arithmetic", "contradiction",
              "reasoning_action", "error_reuse")
