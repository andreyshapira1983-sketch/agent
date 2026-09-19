"""An action that keeps failing without doing work is not retried forever.

24h run 2026-09-19: `run_claim_experiment` failed 25 times in 12 minutes with
«the effect reproduced in neither arm» — zero model calls, no product. The
signature bank and the per-action step ceiling both count only an action that
RAN, so neither saw it, and the priority table put it first every cycle.
After three consecutive failures without work the action is skipped as a
repeat, with a reason saying why.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import _MAX_FAILED_REPEATS, CampaignActionOutcome, CampaignConfig, run_campaign


class _Gather:
    def __call__(self, agent, workspace, approval_inbox):
        return {"action": BestNextAction(
            action="run_claim_experiment", title="Prove a cause by removing it",
            severity="medium", priority=47, reason="open causal claims", risk="reversible")}


class _AlwaysInconclusive:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.calls += 1
        return CampaignActionOutcome(result="failed")


def _run(tmp_path: Path, execute, cycles: int = 12):
    return run_campaign(
        CampaignConfig(goal="g", max_cycles=cycles, max_unproductive_streak=0),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=_Gather(), execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 19, 10, 33, tzinfo=timezone.utc),
    )


def test_three_failures_then_the_action_is_skipped(tmp_path: Path) -> None:
    execute = _AlwaysInconclusive()
    result = _run(tmp_path, execute)
    assert execute.calls == _MAX_FAILED_REPEATS, (
        f"the failing action ran {execute.calls} times — the 25-in-12-minutes loop"
    )
    repeats = [r for r in result.records if r.result == "repeat"]
    assert repeats and "failed 3 times in a row" in repeats[0].reason
