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


# ══ S5 — subsystem disagreement ═════════════════════════════════════════════
# Each declared conflict kind, and the negatives that must stay quiet.

def _kinds(events):
    return [e["kind"] for e in events]


def test_s5_citations_the_verifier_cannot_match():
    events = detect_disagreements(
        attempt=1, plan_steps=[_Step("done"), _Step("done")], artifacts={"a": 1},
        report=_R(total_chunks=10, verified_chunks=5, cited_but_unmatched_chunks=4),
    )
    assert _kinds(events) == ["planner_vs_verifier_partial"]
    assert events[0]["severity"] == "medium"
    assert events[0]["unmatched_ratio"] > events[0]["threshold"]
