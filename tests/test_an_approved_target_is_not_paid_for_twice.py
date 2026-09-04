"""An approved target is not paid for twice (measured 2026-09-04, evening).

OpenAI came back at 19:20 MSK after a day of «no credits». In 54 minutes the
campaign spent $0.65 on 24 gpt-5.6 calls. Nine cycles ended «completed /
work_done», zero approval items were filed, and three of the nine ran under
the goal «Implement the approved split of X» — X being a split the operator
had APPROVED that afternoon. The order of events inside one such cycle:

  1. the LLM run (planner + synthesizer, ~25k tokens) — PAID;
  2. `_propose_engineering_step` → producer says `approval_wait`, target
     already under an approved item — free, and correct;
  3. the goal task's prose («next step: a human should approve…») counts as
     work, so the ledger says work_done=True.

Three repairs, each pinned here:
  - the wait is read BEFORE the run and costs nothing (`approval_wait`, 0
    calls, no product);
  - a recent human denial is read the same way (block 3 cooldown);
  - for the engineering action, work_done follows the PRODUCT (an inbox item),
    never the prose;
  - the charter's verdict block tells him that approved items are applied by
    the operator's lane, not by him.

Doubt does not refuse: an inbox without the reader, or one that raises,
answers «unknown» and the cycle proceeds exactly as before.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.campaign_types import CampaignConfig


def _agent() -> SimpleNamespace:
    budget_ledger = SimpleNamespace(
        snapshot=lambda: {"totals": {"llm_calls": 0, "model_cost_units": 0}}
    )
    return SimpleNamespace(
        model_router=SimpleNamespace(usage_ledger=SimpleNamespace(budget_ledger=budget_ledger)),
        log=SimpleNamespace(log=lambda *a, **k: None),
    )


def _action(target: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        action="propose_engineering_task", title="Turn a real backlog candidate into a reviewed proposal",
        severity="medium", priority=40, reason="goal asks for engineering work",
        evidence=(), unknowns=(), risk="reversible", recommended_command=None,
        confidence=0.7, target_path=target, grounds="operator_goal", decided_by="rule",
    )


class _Inbox:
    def __init__(self, waiting=frozenset(), denied=frozenset(), *, raises=False):
        self._waiting, self._denied, self._raises = waiting, denied, raises

    def pending(self):
        return []

    def pending_targets(self, *, operation=None):
        if self._raises:
            raise OSError("inbox unreadable")
        return self._waiting

    def recently_denied_targets(self, *, operation=None):
        return self._denied


def _forbid_runtime(monkeypatch) -> dict:
    import core.autonomous_runtime as ar

    seen: dict = {"runs": 0}

    class _Runtime:
        def __init__(self, agent, *, workspace, approval_inbox=None):
            pass

        def run(self, config):
            seen["runs"] += 1
            raise AssertionError("the model must not be paid for a target that already waits")

    monkeypatch.setattr(ar, "AutonomousRuntime", _Runtime)
    return seen


def test_a_target_under_an_approved_item_is_refused_before_any_model_call(monkeypatch, tmp_path):
    from core.campaign import _default_execute_action

    seen = _forbid_runtime(monkeypatch)
    inbox = _Inbox(waiting=frozenset({"core/smart_memory.py"}))
    config = CampaignConfig(goal="Implement the approved split of core/smart_memory.py", dry_run=False)

    outcome = _default_execute_action(
        agent=_agent(), workspace=tmp_path, action=_action("core/smart_memory.py"),
        config=config, approval_inbox=inbox,
    )

    assert seen["runs"] == 0
    assert outcome.result == "approval_wait"
    assert outcome.llm_calls_spent == 0 and outcome.cost_units_spent == 0
    assert outcome.did_work is False and outcome.proposal is None and outcome.artifact is None
    assert outcome.subject == "core/smart_memory.py"


def test_a_recently_denied_target_cools_down_before_any_model_call(monkeypatch, tmp_path):
    from core.campaign import _default_execute_action

    seen = _forbid_runtime(monkeypatch)
    inbox = _Inbox(denied=frozenset({"core/reflection.py"}))
    config = CampaignConfig(goal="Repair core/reflection.py", dry_run=False)

    outcome = _default_execute_action(
        agent=_agent(), workspace=tmp_path, action=_action("core/reflection.py"),
        config=config, approval_inbox=inbox,
    )

    assert seen["runs"] == 0
    assert outcome.result == "approval_wait" and outcome.did_work is False


@pytest.mark.parametrize("inbox", [
    _Inbox(waiting=frozenset({"core/other.py"})),   # another file waits, not this one
    _Inbox(raises=True),                             # unreadable inbox = unknown
])
def test_doubt_does_not_refuse_the_run_proceeds_as_before(monkeypatch, tmp_path, inbox):
    import core.campaign_io as cio
    from core.campaign import _default_execute_action

    seen = _forbid_runtime(monkeypatch)
    # The run itself is not under test here: it is reached, and that is the point.
    with pytest.raises(AssertionError, match="must not be paid"):
        _default_execute_action(
            agent=_agent(), workspace=tmp_path, action=_action("core/smart_memory.py"),
            config=CampaignConfig(goal="Split core/smart_memory.py", dry_run=False),
            approval_inbox=inbox,
        )
    assert seen["runs"] == 1
    assert cio._engineering_target_waits(inbox, "core/smart_memory.py") == ""


def test_prose_is_not_work_for_the_engineering_action_only_a_filed_item_is(monkeypatch, tmp_path):
    """The goal task answered «next step: a human should approve…» and the
    producer refused: no product, so no work — whatever the run report says."""
    import core.autonomous_runtime as ar
    import core.campaign_io as cio
    from core.autonomous_runtime_types import AutonomousRunReport
    from core.campaign import _default_execute_action

    class _Runtime:
        def __init__(self, agent, *, workspace, approval_inbox=None):
            pass

        def run(self, config):
            goal_task = SimpleNamespace(
                task=SimpleNamespace(kind="goal"), status="done",
                details={"answer": "Следующий шаг: человек должен утвердить инженерную задачу."},
            )
            return AutonomousRunReport(
                status="completed", dry_run=False, goal="g", tasks=[goal_task],
                budget={}, circuit={}, approvals={"pending": 0},
            )

    monkeypatch.setattr(ar, "AutonomousRuntime", _Runtime)
    monkeypatch.setattr(cio, "_propose_engineering_step", lambda **kw: None)
    monkeypatch.setattr(cio, "_propose_repair_from_diagnosis", lambda **kw: None)
    inbox = _Inbox()

    outcome = _default_execute_action(
        agent=_agent(), workspace=tmp_path, action=_action("core/smart_memory.py"),
        config=CampaignConfig(goal="Split core/smart_memory.py", dry_run=False),
        approval_inbox=inbox,
    )

    assert outcome.result == "completed"
    assert outcome.work_done is False and outcome.proposal is None
    # The reasoning digest is NOT the product of the engineering action: as an
    # artifact it made the cycle «productive» (did_work, streak reset) and kept
    # the goal alive. It goes to the log, not the ledger.
    assert outcome.artifact is None
    assert outcome.did_work is False


def test_the_charter_hears_that_approved_items_are_the_lanes_not_his():
    import inspect

    from core import charter_goal

    src = inspect.getsource(charter_goal)
    assert "APPROVED items are applied by the operator's lane" in src
    assert 'if v == "approved"' in src
