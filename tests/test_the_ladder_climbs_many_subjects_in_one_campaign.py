"""One campaign now walks the ladder across MANY subjects, not one per launch.

Measured 2026-08-30: the repeat guard's signature was the bare action name, so
after one explain the second cycle was recorded «already attempted» — one
campaign advanced the ladder by exactly one step, and 46 observations meant 46
manual relaunches. Meanwhile the guard's protection was real: the judge failing
three times on ONE poisoned claim was honestly stopped by the stall sensor.

The redesign is the agent's, over two rounds (DEDUP_DESIGN + DEDUP_TIMING):
the SUBJECT is born in the outcome, so the pre-execution guard passes a
subject-aware action while its per-campaign step counter is under the ceiling
(10), and the composite signature «action:subject» is banked after execution —
a subject that comes back already banked means the queue did not advance, and
that feeds the stall sensor. Actions without a subject keep the old bare-name
ban to the letter. Scenarios here follow his design sections 1–4; the harness
reuses the scripted fakes of tests/test_campaign.py (courier work — fixture
files are past the one-delivery ceiling, banked in the experiment ledger).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig


def _explain_action() -> BestNextAction:
    return BestNextAction(
        action="explain_causal_observation",
        title="Explain one of the agent's own recorded failures",
        severity="medium",
        priority=45,
        reason="unexplained observations await",
        risk="reversible",
    )


def _plain_action() -> BestNextAction:
    return BestNextAction(
        action="draft_doctrine_document",
        title="Draft the doctrine document",
        severity="medium",
        priority=58,
        reason="the goal asks for a doctrine draft",
        risk="reversible",
    )


class _AlwaysGather:
    def __init__(self, action: BestNextAction):
        self._action = action

    def __call__(self, agent, workspace, approval_inbox):
        return {"action": self._action}


class _SequencedExecute:
    """Returns scripted outcomes in order; repeats the last one forever."""

    def __init__(self, outcomes: list[CampaignActionOutcome]):
        self._outcomes = outcomes
        self.calls = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        idx = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        return self._outcomes[idx]


def _outcome(subject: str) -> CampaignActionOutcome:
    return CampaignActionOutcome(
        result="completed", llm_calls_spent=1, subject=subject, work_done=True,
    )


def _now():
    return datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _run(action, execute, *, max_cycles=8, max_idle_streak=3):
    return run_campaign(
        CampaignConfig(
            max_cycles=max_cycles, max_idle_streak=max_idle_streak,
            dry_run=False,
        ),
        agent=SimpleNamespace(log=None),
        workspace="/tmp/ws",
        gather_signals=_AlwaysGather(action),
        execute_action=execute,
        ledger=CampaignLedger(),
        now_fn=_now,
    )


def test_fresh_subjects_keep_the_ladder_climbing(tmp_path):
    """His core promise: three different subjects — three executions, no repeat."""
    execute = _SequencedExecute([_outcome("obs1"), _outcome("obs2"), _outcome("obs3")])

    result = _run(_explain_action(), execute, max_cycles=3)

    assert execute.calls == 3
    assert [r.result for r in result.records] == ["completed"] * 3


def test_a_stuck_subject_feeds_the_stall_sensor(tmp_path):
    """The same subject coming back means the queue did not advance."""
    execute = _SequencedExecute([_outcome("obs1")])  # obs1 forever

    result = _run(_explain_action(), execute, max_cycles=8, max_idle_streak=3)

    assert result.status == "stopped"
    assert "no_progress_stall" in result.stop_reason
    # The first pass banked obs1; the stalls started from the second.
    assert execute.calls <= 5


def test_the_step_ceiling_stops_a_monopolist(tmp_path):
    """His ceiling: at most 10 executed steps of one action per campaign."""
    outcomes = [_outcome(f"obs{i}") for i in range(1, 15)]
    execute = _SequencedExecute(outcomes)

    result = _run(_explain_action(), execute, max_cycles=14)

    assert execute.calls == 10
    ceiling_repeats = [r for r in result.records if "потолок шагов" in (r.reason or "")]
    assert ceiling_repeats, "the ceiling never fired"


def test_a_subjectless_action_keeps_the_old_bare_name_ban(tmp_path):
    """Regression: non-subject actions are banked by bare name, as before."""
    execute = _SequencedExecute([CampaignActionOutcome(
        result="completed", llm_calls_spent=1, work_done=True,
    )])

    result = _run(_plain_action(), execute, max_cycles=4)

    assert execute.calls == 1
    repeats = [r for r in result.records if r.result == "repeat"]
    assert repeats and "already attempted" in (repeats[0].reason or "")
