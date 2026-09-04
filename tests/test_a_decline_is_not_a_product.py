"""A decline is not a product; a permission question is not a proposal.

Measured 2026-09-04. Two counters lied in the campaign ledger:

  - the hands (repair / doctrine draft / hypothesis / engineering) return a
    STRING either way — «repair_proposed:<id>» when something was created and
    «doc_declined:doc_exists» / «repair_declined:…» / «engineering_error:…»
    when nothing was. Both went into `proposal`, so a cycle that created
    nothing was «productive»: did_work=True, the no-product streak reset,
    the goal stayed alive;
  - `approvals_new=N` counted every new pending inbox item, including the
    agent's own «may I have effects?» question. The 40/day grant burned by
    12:28 on 29 such questions, each one a «useful cycle».

Here the word follows the thing: only a created item or a recorded
hypothesis is a product; a reason goes to the log by name.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.campaign_types import CampaignConfig


def _agent(events: list | None = None) -> SimpleNamespace:
    budget_ledger = SimpleNamespace(
        snapshot=lambda: {"totals": {"llm_calls": 0, "model_cost_units": 0}}
    )
    sink = events if events is not None else []
    log = SimpleNamespace(log=lambda ev, payload=None, **k: sink.append((ev, payload)))
    return SimpleNamespace(
        model_router=SimpleNamespace(usage_ledger=SimpleNamespace(budget_ledger=budget_ledger)),
        log=log,
    )


@pytest.mark.parametrize("said", [
    "doc_declined:doc_exists", "doc_declined:no_target_doc", "repair_declined:vetoed",
    "engineering_error:ValueError", "superseded_by_existing:ain_1", "refused_repeat_of_denied:ain_2",
    "hypothesis_declined:no_web_citations", "test_declined:no_target", "test_proposal_error", "",
])
def test_a_reason_is_logged_by_name_and_is_not_a_product(said):
    from core.campaign_io import _product

    events: list = []
    assert _product(_agent(events), said) is None
    if said:
        assert events and events[-1][0] == "campaign_hands_declined"
        assert events[-1][1]["reason"] == said


@pytest.mark.parametrize("said", [
    "approvals_new=1", "repair_proposed:ain_3", "engineering_proposed:ain_4",
    "doc_draft_proposed:ain_5", "hypothesis_recorded:key", "test_proposed:ain_6",
])
def test_a_created_thing_is_a_product(said):
    from core.campaign_io import _product

    assert _product(_agent(), said) == said


class _Inbox:
    """pending() answers differently before and after the run."""

    def __init__(self, after):
        self._after, self._flipped = after, False

    def pending(self):
        return self._after if self._flipped else []

    def flip(self):
        self._flipped = True


def _run_with(monkeypatch, inbox, *, answer=None):
    import core.autonomous_runtime as ar
    from core.autonomous_runtime_types import AutonomousRunReport

    class _Runtime:
        def __init__(self, agent, *, workspace, approval_inbox=None):
            pass

        def run(self, config):
            inbox.flip()
            task = SimpleNamespace(
                task=SimpleNamespace(kind="goal"), status="done",
                details={"answer": answer} if answer else {},
            )
            return AutonomousRunReport(
                status="completed", dry_run=False, goal="g", tasks=[task],
                budget={}, circuit={}, approvals={"pending": 0},
            )

    monkeypatch.setattr(ar, "AutonomousRuntime", _Runtime)


def _action(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        action=name, title=name, severity="medium", priority=40, reason="r",
        evidence=(), unknowns=(), risk="reversible", recommended_command=None,
        confidence=0.7, target_path=None, grounds="operator_goal", decided_by="rule",
    )


def test_a_permission_question_born_in_the_run_is_not_a_new_proposal(monkeypatch, tmp_path):
    import core.campaign_io as cio
    from core.campaign import _default_execute_action

    question = SimpleNamespace(id="ain_q", operation="autonomous_runtime.allow_effects")
    inbox = _Inbox(after=[question])
    _run_with(monkeypatch, inbox)
    monkeypatch.setattr(cio, "_propose_repair_from_diagnosis", lambda **kw: None)

    outcome = _default_execute_action(
        agent=_agent(), workspace=tmp_path, action=_action("investigate_tick_error"),
        config=CampaignConfig(goal="Investigate", dry_run=False), approval_inbox=inbox,
    )

    assert outcome.proposal is None


def test_a_real_item_born_in_the_run_still_counts(monkeypatch, tmp_path):
    import core.campaign_io as cio
    from core.campaign import _default_execute_action

    item = SimpleNamespace(id="ain_real", operation="self_apply_lane.run")
    inbox = _Inbox(after=[item])
    _run_with(monkeypatch, inbox)
    monkeypatch.setattr(cio, "_propose_repair_from_diagnosis", lambda **kw: None)

    outcome = _default_execute_action(
        agent=_agent(), workspace=tmp_path, action=_action("investigate_tick_error"),
        config=CampaignConfig(goal="Investigate", dry_run=False), approval_inbox=inbox,
    )

    assert outcome.proposal == "approvals_new=1"
    assert outcome.did_work is True


def test_a_declined_repair_leaves_the_cycle_without_a_product(monkeypatch, tmp_path):
    import core.campaign_io as cio
    from core.campaign import _default_execute_action

    inbox = _Inbox(after=[])
    _run_with(monkeypatch, inbox)
    monkeypatch.setattr(cio, "_propose_repair_from_diagnosis", lambda **kw: "repair_declined:vetoed")

    outcome = _default_execute_action(
        agent=_agent(), workspace=tmp_path, action=_action("investigate_tick_error"),
        config=CampaignConfig(goal="Investigate", dry_run=False), approval_inbox=inbox,
    )

    assert outcome.proposal is None
    assert outcome.artifact is None
