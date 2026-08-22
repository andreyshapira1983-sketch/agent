"""MIR-035 audit: merging by class must not lose how often the class fired.

The closure merged N detector failures of one signal class into one issue —
correct, the repair ladder repairs classes, not turns. The field's named
failure for merge-by-class is that distinct defects hide under one row, and
the probe found the real half of it: evidence is capped at 8 samples, so a
class that fired 50 times is indistinguishable from one that fired 8, and
recurrence is precisely what makes a class worth investigating.

The count must be idempotent: the producer re-reads the last 7 days of events
on every sweep, so counting calls would count sweeps instead of failures.
"""
from __future__ import annotations

from pathlib import Path

from core.self_improvement_issues import SelfImprovementIssueRegistry


def _sweep(reg: SelfImprovementIssueRegistry, count: int) -> None:
    for i in range(count):
        reg.upsert_failure(
            f"detectors content_refuted: вопрос {i} про разный предмет {i}",
            f"2026-08-{10 + i:02d}T00:00:00+00:00",
        )


def test_the_count_survives_the_evidence_cap(tmp_path: Path) -> None:
    reg = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    _sweep(reg, 10)

    issue = reg.list()[0]
    assert len(issue.evidence) == 8, "the cap is the premise of this test"
    assert issue.occurrences == 10, (
        "ten failures of one class read as eight — the merge kept the class "
        "and threw away its magnitude"
    )


def test_re_reading_the_same_window_does_not_inflate(tmp_path: Path) -> None:
    """`update_self_improvement_issues` re-reads a 7-day window every sweep."""
    reg = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    _sweep(reg, 10)
    _sweep(reg, 10)

    assert reg.list()[0].occurrences == 10, (
        "the second sweep over the same events counted itself — the sensor "
        "measures how often it runs, not how often the agent failed"
    )


def test_the_proposed_action_states_the_count(tmp_path: Path) -> None:
    """A number nobody reads is not a repair."""
    from core.best_next_action import _candidate_open_self_improvement_issue

    reg = SelfImprovementIssueRegistry(tmp_path / "issues.jsonl")
    _sweep(reg, 10)

    action = _candidate_open_self_improvement_issue(
        tuple(i.to_dict() for i in reg.unresolved())
    )
    assert action is not None
    assert any("seen=10x" in e for e in action.evidence), action.evidence
