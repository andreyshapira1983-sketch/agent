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

from core.autonomous_runtime import (
    AutonomousQueuedTaskReport,
    AutonomousQueueRunReport,
)


def _task(status: str, task_id: str = "t1") -> AutonomousQueuedTaskReport:
    return AutonomousQueuedTaskReport(
        task_id=task_id, goal="probe", status=status, run_status="", summary=""
    )


# --- the honest fact must reach the record, not only the object --------------


def test_the_human_summary_stops_claiming_success_it_did_not_have() -> None:
    """What a person reads must not say 'completed' about a run where nothing
    worked. The approval preview is a measured weak point (OWASP ASI09), so a
    summary that overstates is not cosmetic."""
    report = AutonomousQueueRunReport(status="completed", processed=[_task("failed")])
    text = report.user_summary().lower()
    assert "failed=1" in text or "no work succeeded" in text, (
        f"the summary hides the failure: {text!r}"
    )
