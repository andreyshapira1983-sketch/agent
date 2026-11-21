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


class TestDetectDisagreements:
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
