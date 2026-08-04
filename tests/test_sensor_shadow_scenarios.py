"""S2 and S5 — the targeted scenarios they never got on real traffic.

Measurement showed both sensors fired 0/14 on the recorded cycles, and that
this said nothing about them: 11 of 14 turns were tool-free and none replanned,
so neither was ever in a position to fire
(`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`). Operator ruling: do not delete,
build the scenarios, then shadow-log what a connected sensor *would* have done
— and stop nothing.

So this suite is the missing opportunity, built deliberately: each declared
detection case, each declared non-case, and the shadow accounting that turns
"it fired" into "here is what acting on it would have cost".
"""
from __future__ import annotations

from dataclasses import dataclass

from core.subsystem_disagreement import detect_disagreements
from core.termination_guard import TerminationGuard


@dataclass
class _R:
    total_chunks: int = 0
    verified_chunks: int = 0
    unverified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    fully_unverified: bool = False
    chain_was_empty: bool = False


@dataclass
class _Step:
    status: str


# ══ S2 — stagnation ═════════════════════════════════════════════════════════
# The five scenarios named in the ruling.

def test_s2_identical_signature_twice_is_stagnation():
    """1. Same failure codes, artifacts unchanged."""
    g = TerminationGuard()
    assert g.observe_attempt(attempt=1, failure_codes=["tool_error"],
                             artifact_labels=["a"]) is None
    event = g.observe_attempt(attempt=2, failure_codes=["tool_error"],
                              artifact_labels=["a"])
    assert event is not None
    assert event.repeat_count == 2
    assert event.failure_codes == ("tool_error",)


def test_s2_same_error_but_new_artifacts_is_progress_not_stagnation():
    """2. The error repeats, but the run gained something. Not a loop."""
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["tool_error"], artifact_labels=["a"])
    assert g.observe_attempt(attempt=2, failure_codes=["tool_error"],
                             artifact_labels=["a", "b"]) is None


def test_s2_same_artifacts_but_a_different_cause_is_not_an_identical_loop():
    """3. Same standing, different reason — the run is still exploring."""
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["tool_error"], artifact_labels=["a"])
    assert g.observe_attempt(attempt=2, failure_codes=["web_empty"],
                             artifact_labels=["a"]) is None


def test_s2_repeat_after_a_meaningful_strategy_change_rearms_but_does_not_fire():
    """4. A real change resets the detector; the next repeat is a fresh pair."""
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["tool_error"], artifact_labels=[])
    # Strategy changed: different failure, different artifacts.
    assert g.observe_attempt(attempt=2, failure_codes=["web_empty"],
                             artifact_labels=["b"]) is None
    # One occurrence of the NEW signature is not yet a loop.
    assert g.observe_attempt(attempt=3, failure_codes=["tool_error"],
                             artifact_labels=[]) is None


def test_s2_meaningless_restart_of_the_same_plan_fires_once_only():
    """5. Re-running the same broken plan: one edge, not a stream."""
    g = TerminationGuard()
    g.observe_attempt(attempt=1, failure_codes=["tool_error"], artifact_labels=[])
    assert g.observe_attempt(attempt=2, failure_codes=["tool_error"],
                             artifact_labels=[]) is not None
    assert g.observe_attempt(attempt=3, failure_codes=["tool_error"],
                             artifact_labels=[]) is None, "must not re-fire"
    assert g.observe_attempt(attempt=4, failure_codes=["tool_error"],
                             artifact_labels=[]) is None


# ══ S5 — subsystem disagreement ═════════════════════════════════════════════
# Each declared conflict kind, and the negatives that must stay quiet.

def _kinds(events):
    return [e["kind"] for e in events]


def test_s5_planner_all_done_but_verifier_fully_unverified():
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("done"), _Step("done")],
        artifacts={"a": 1},
        report=_R(total_chunks=5, unverified_chunks=5, fully_unverified=True),
    )
    assert _kinds(events) == ["planner_vs_verifier_full"]
    assert events[0]["severity"] == "high"


def test_s5_planner_planned_tools_but_executor_produced_nothing():
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("failed"), _Step("failed")], artifacts={},
        report=_R(total_chunks=3, verified_chunks=1, unverified_chunks=2),
    )
    assert "planner_vs_executor" in _kinds(events)
    assert next(e for e in events if e["kind"] == "planner_vs_executor")["severity"] == "low"


def test_s5_citations_the_verifier_cannot_match():
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("done"), _Step("done")], artifacts={"a": 1},
        report=_R(total_chunks=10, verified_chunks=5, cited_but_unmatched_chunks=4),
    )
    assert _kinds(events) == ["planner_vs_verifier_partial"]
    assert events[0]["severity"] == "medium"
    assert events[0]["unmatched_ratio"] > events[0]["threshold"]


def test_s5_pure_general_knowledge_is_not_a_disagreement():
    """Negative: verifier honestly says "no source", planner honestly planned none."""
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("done"), _Step("done")], artifacts={},
        report=_R(total_chunks=5, unverified_chunks=5, fully_unverified=True,
                  chain_was_empty=True),
    )
    assert events == []


def test_s5_a_tool_that_failed_and_was_marked_failed_is_not_an_all_done_conflict():
    """Negative: the planner did not claim success, so there is no contradiction."""
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("failed"), _Step("done")], artifacts={"a": 1},
        report=_R(total_chunks=5, unverified_chunks=5, fully_unverified=True),
    )
    assert "planner_vs_verifier_full" not in _kinds(events)


def test_s5_everything_verified_is_quiet():
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("done")], artifacts={"a": 1},
        report=_R(total_chunks=5, verified_chunks=5),
    )
    assert events == []


def test_s5_one_broken_citation_in_a_long_answer_does_not_spam():
    """Below the 30% threshold, deliberately."""
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("done")], artifacts={"a": 1},
        report=_R(total_chunks=20, verified_chunks=19, cited_but_unmatched_chunks=1),
    )
    assert events == []
