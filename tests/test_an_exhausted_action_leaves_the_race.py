"""An action that hit its step ceiling leaves the race, and the next weight runs.

Measured 2026-08-31 (campaign day2): explain took its tenth step, the chooser —
blind to the campaign-side ceiling — kept crowning it at weight 45, the
campaign recorded three «потолок шагов» repeats and stalled, and the birth
action (44) with a live candidate never got a cycle. The agent's own earlier
discovery, reproduced at the seam of his own two mechanisms: the loudest wins,
not the one with work to do.

The fix is his verdict (CEILING_VERDICT, path (а)): the knowledge is born in
the campaign's `action_steps`, travels to the gatherer by the same tolerant
call as the goal, and the chooser refuses exhausted candidates at admit time.
Critical candidates are untouched BY CONSTRUCTION: only subject-aware names
from `action_steps` can enter the set. Scenarios below are his witness order
(EXHAUSTED_WEAVE §4); the harness follows tests/test_campaign.py's scripted
fakes (courier assembly, per the banked fixture-ceiling protocol).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from core.best_next_action import select_best_next_action
from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig


def test_an_exhausted_explain_yields_to_the_next_weight():
    """His check (i): explain is excluded, the race crowns the runner-up."""
    crowned = select_best_next_action(
        unexplained_observations_count=5,
        birth_candidates=1,
        exhausted_actions={"explain_causal_observation"},
    )

    assert crowned.action != "explain_causal_observation"
    assert crowned.action == "birth_experiment_specs"


def test_an_empty_set_changes_nothing():
    """His check (ii): the old behaviour to the letter."""
    crowned = select_best_next_action(
        unexplained_observations_count=5,
        birth_candidates=1,
        exhausted_actions=frozenset(),
    )

    assert crowned.action == "explain_causal_observation"


class _CeilingAwareGather:
    """Returns explain until told it is exhausted, then the birth action."""

    def __init__(self):
        self.calls = 0

    def __call__(self, agent, workspace, approval_inbox, goal="",
                 exhausted_actions=frozenset()):
        self.calls += 1
        return {"action": select_best_next_action(
            unexplained_observations_count=20,
            birth_candidates=1,
            exhausted_actions=exhausted_actions,
        )}


class _SubjectExecute:
    def __init__(self):
        self.calls = []

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.calls.append(action.action)
        return CampaignActionOutcome(
            result="completed", llm_calls_spent=1,
            subject=f"s{len(self.calls)}", work_done=True,
        )


def test_a_saturated_explain_gives_birth_its_cycle():
    """His check (iii): the ceiling hands the cycle onward instead of repeating."""
    gather = _CeilingAwareGather()
    execute = _SubjectExecute()

    result = run_campaign(
        CampaignConfig(max_cycles=12, max_idle_streak=3, dry_run=False),
        agent=SimpleNamespace(log=None),
        workspace="/tmp/ws",
        gather_signals=gather,
        execute_action=execute,
        ledger=CampaignLedger(),
        now_fn=lambda: datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert execute.calls[:10] == ["explain_causal_observation"] * 10
    assert "birth_experiment_specs" in execute.calls[10:]
    ceiling_repeats = [r for r in result.records if "потолок" in (r.reason or "")]
    assert ceiling_repeats == []
