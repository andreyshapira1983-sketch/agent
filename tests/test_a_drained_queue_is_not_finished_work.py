"""Norm A, ratified 2026-08-22: the agent itself must distinguish
"processing finished" from "the task was actually done".

Measured as MIR-117: `AutonomousRuntime` sets its queue status to
`"completed" if processed` — PROCESSED, not SUCCEEDED. A task that failed still
increments `processed`, so `completed` means "the queue drained and nothing
stopped us", never "the work happened". `core/campaign_io.py` then copies that
status verbatim into a cycle result, and `cli/commands_approval.py` burns the
operator's one-shot approval on it.

Observed live on 2026-08-21: a campaign whose only substantive task was refused
pre-flight by the cost envelope still recorded `result=completed`,
`useful_cycles=1`. The run did nothing and was counted as having done
something.

The operator's ruling, recorded in the registry, is why this is a defect and
not a naming preference: *the request for permission, the permission itself,
and the actual execution are three different events and must not merge into one
status* — and the agent needs the distinction for its OWN behaviour, not so a
human can approve each step.

WHAT THIS FIXES AND WHAT IT DOES NOT. It gives the report a second, honest
fact derived from data it already holds: each processed task carries its own
lifecycle status (`done` / `failed` / `blocked` / ...). The queue-level
`status` keeps its existing meaning — queue lifecycle — because renaming a
field read across the codebase would be a different and larger change. What is
added is the answer to "did any of it actually work", which nothing could ask
before.

This does NOT rekey the approval burn (MIR-118 must first settle whether that
approval belongs there at all), and it does not touch who DECIDED anything —
that is the separate provenance gap where authorship, evidence origin and
reviewer identity are still absent.
"""
from __future__ import annotations

import pytest

from core.autonomous_runtime import (
    AutonomousQueuedTaskReport,
    AutonomousQueueRunReport,
)


def _task(status: str, task_id: str = "t1") -> AutonomousQueuedTaskReport:
    return AutonomousQueuedTaskReport(
        task_id=task_id, goal="probe", status=status, run_status="", summary=""
    )


# --- the distinction itself ---------------------------------------------------

def test_a_drained_queue_of_failures_does_not_read_as_work_done() -> None:
    """The heart of norm A. Every task failed; the queue still drained."""
    report = AutonomousQueueRunReport(
        status="completed",
        processed=[_task("failed", "a"), _task("failed", "b")],
    )
    assert report.status == "completed", "queue lifecycle is unchanged by design"
    assert report.work_succeeded is False, (
        "two failed tasks reported as succeeded work — the exact conflation "
        "MIR-117 measured"
    )
    assert report.failed_count == 2
    assert report.succeeded_count == 0


def test_real_work_is_reported_as_real_work() -> None:
    """The control that must stay green: a fix that always says 'no work' is
    as useless as one that always says 'completed'."""
    report = AutonomousQueueRunReport(
        status="completed", processed=[_task("done", "a"), _task("done", "b")]
    )
    assert report.work_succeeded is True
    assert report.succeeded_count == 2
    assert report.failed_count == 0


def test_a_mixed_run_is_neither_a_success_nor_a_lie() -> None:
    """One worked, one did not. Both facts survive; neither hides the other."""
    report = AutonomousQueueRunReport(
        status="completed", processed=[_task("done", "a"), _task("failed", "b")]
    )
    assert report.succeeded_count == 1
    assert report.failed_count == 1
    assert report.work_succeeded is True, "something genuinely did work"
    assert report.work_partial is True, "and something genuinely did not"


def test_an_empty_queue_claims_nothing() -> None:
    report = AutonomousQueueRunReport(status="empty", processed=[])
    assert report.work_succeeded is False
    assert report.work_partial is False
    assert report.succeeded_count == 0 and report.failed_count == 0


@pytest.mark.parametrize("status,succeeded", [
    ("done", True),
    ("failed", False),
    ("blocked", False),      # waiting on a human is not work performed
    ("cancelled", False),
    ("paused", False),
    ("pending", False),
    ("running", False),      # still going is not finished
])
def test_every_lifecycle_status_lands_on_the_honest_side(
    status: str, succeeded: bool
) -> None:
    """`blocked` is the interesting one: MIR-039 made it a resting state that is
    neither success nor failure, and it must not drift into the success column
    merely because nothing crashed."""
    report = AutonomousQueueRunReport(status="completed", processed=[_task(status)])
    assert report.work_succeeded is succeeded


# --- the honest fact must reach the record, not only the object --------------

def test_the_two_facts_are_both_serialised() -> None:
    """A distinction that never leaves the process cannot be audited later —
    the whole point of the five-axis provenance work.

    Both polarities are asserted deliberately. The first version of this test
    checked only a run where work HAD succeeded, so hardcoding
    `"work_succeeded": True` in the serialiser passed it — a test that could
    not falsify its own claim, caught by breaking the fix on purpose."""
    mixed = AutonomousQueueRunReport(
        status="completed", processed=[_task("done", "a"), _task("failed", "b")]
    ).to_dict()
    assert mixed["status"] == "completed"
    assert mixed["work_succeeded"] is True
    assert mixed["succeeded_count"] == 1
    assert mixed["failed_count"] == 1

    # The polarity that catches a hardcoded serialiser: a drained queue of
    # failures must carry `False` all the way into the record.
    barren = AutonomousQueueRunReport(
        status="completed", processed=[_task("failed", "a"), _task("failed", "b")]
    ).to_dict()
    assert barren["status"] == "completed", "queue lifecycle still says drained"
    assert barren["work_succeeded"] is False, (
        "the record claims work succeeded for a run where every task failed"
    )
    assert barren["succeeded_count"] == 0
    assert barren["failed_count"] == 2


def test_the_human_summary_stops_claiming_success_it_did_not_have() -> None:
    """What a person reads must not say 'completed' about a run where nothing
    worked. The approval preview is a measured weak point (OWASP ASI09), so a
    summary that overstates is not cosmetic."""
    report = AutonomousQueueRunReport(status="completed", processed=[_task("failed")])
    text = report.user_summary().lower()
    assert "failed=1" in text or "no work succeeded" in text, (
        f"the summary hides the failure: {text!r}"
    )
