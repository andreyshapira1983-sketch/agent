"""P1: cross-subsystem disagreement detection.

The detector is a pure function over already-computed subsystem
outputs. We exercise the three v1 cases (full planner/verifier
disagreement, partial citation mismatch, planner/executor empty) plus
the suppression rule for honest "no source consulted" answers.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.subsystem_disagreement import detect_disagreements


@dataclass
class _Step:
    status: str


@dataclass
class _Report:
    total_chunks: int = 0
    verified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    fully_unverified: bool = False
    chain_was_empty: bool = False


@dataclass
class _Trigger:
    code: str


class TestDetectDisagreements:
    def test_planner_done_but_fully_unverified_with_artifacts(self):
        # Planner reports all steps done AND tools were actually run
        # (artifacts non-empty), yet the verifier marks the answer as
        # fully unverified. This is the classic "органы спорят" case.
        events = detect_disagreements(
            attempt=1,
            plan_steps=[_Step("done"), _Step("done")],
            artifacts={"file:a.md": {}},
            report=_Report(
                total_chunks=3,
                verified_chunks=0,
                cited_but_unmatched_chunks=2,
                fully_unverified=True,
                chain_was_empty=False,
            ),
        )
        assert len(events) == 1
        ev = events[0]
        assert ev["kind"] == "planner_vs_verifier_full"
        assert ev["severity"] == "high"
        assert ev["subsystems"] == ["planner", "verifier"]
        assert ev["planner_steps_done"] == 2
        assert ev["verifier_fully_unverified"] is True

    def test_no_event_when_chain_empty_and_no_artifacts(self):
        # Pure prior-knowledge answer: planner planned nothing, no
        # tools ran, no chain. Verifier says "fully unverified" but
        # this is honest behaviour, not a subsystem conflict — the
        # disclaimer pathway already labels it. Suppress the event.
        events = detect_disagreements(
            attempt=1,
            plan_steps=[_Step("done")],
            artifacts={},
            report=_Report(
                total_chunks=2,
                fully_unverified=True,
                chain_was_empty=True,
            ),
        )
        assert events == []

    def test_partial_citation_mismatch(self):
        # 4 of 10 chunks cite a source the verifier could not match
        # (40% > 30% threshold).
        events = detect_disagreements(
            attempt=1,
            plan_steps=[_Step("done")],
            artifacts={"x": {}},
            report=_Report(
                total_chunks=10,
                verified_chunks=6,
                cited_but_unmatched_chunks=4,
                fully_unverified=False,
            ),
        )
        assert len(events) == 1
        ev = events[0]
        assert ev["kind"] == "planner_vs_verifier_partial"
        assert ev["severity"] == "medium"
        assert ev["unmatched_ratio"] == 0.4

    def test_partial_below_threshold_no_event(self):
        # 1 of 10 chunks unmatched (10% < 30% threshold) — no event.
        events = detect_disagreements(
            attempt=1,
            plan_steps=[_Step("done")],
            artifacts={"x": {}},
            report=_Report(
                total_chunks=10,
                verified_chunks=9,
                cited_but_unmatched_chunks=1,
                fully_unverified=False,
            ),
        )
        assert events == []

    def test_planner_vs_executor_empty(self):
        # Planner produced 2 steps, all failed, zero artifacts.
        events = detect_disagreements(
            attempt=2,
            plan_steps=[_Step("failed"), _Step("failed")],
            artifacts={},
            report=_Report(total_chunks=1, fully_unverified=True, chain_was_empty=True),
            failure_history=[_Trigger("tool_error"), _Trigger("timeout")],
        )
        # No verifier disagreement (suppressed: chain empty + no
        # artifacts) but executor disagreement should fire.
        kinds = {e["kind"] for e in events}
        assert "planner_vs_executor" in kinds
        exec_ev = next(e for e in events if e["kind"] == "planner_vs_executor")
        assert exec_ev["severity"] == "low"
        assert exec_ev["failure_codes"] == ["tool_error", "timeout"]

    def test_no_event_on_clean_run(self):
        events = detect_disagreements(
            attempt=1,
            plan_steps=[_Step("done"), _Step("done")],
            artifacts={"a": {}, "b": {}},
            report=_Report(
                total_chunks=5,
                verified_chunks=5,
                cited_but_unmatched_chunks=0,
                fully_unverified=False,
            ),
        )
        assert events == []

    def test_no_report_no_verifier_events(self):
        # Verifier disabled (report=None) — only executor cases can fire.
        events = detect_disagreements(
            attempt=1,
            plan_steps=[_Step("done")],
            artifacts={"x": {}},
            report=None,
        )
        assert events == []

    def test_payload_includes_attempt_and_phase(self):
        events = detect_disagreements(
            attempt=3,
            plan_steps=[_Step("done")],
            artifacts={"x": {}},
            report=_Report(
                total_chunks=2, fully_unverified=True, chain_was_empty=False
            ),
        )
        assert events[0]["attempt"] == 3
        assert events[0]["phase"] == "verify"
