"""A shift outlives its first wall (block 8, operator's word 2026-09-03).

The evening's three runs: 4, 21 and 8 cycles — 5, 35 and 10 minutes of work
inside a ten-hour shift — each ended by the outcome of ONE piece of work
(`idle_stall` after three declined goal switches, `loop_suspected` after
three honest «inexpressible» verdicts, `no_progress_stall`) and slept until
the trigger twelve hours later. Task stopped read as agent stopped.

Now the outcome of a piece of work sends the run back to choosing another
reachable goal; when three switch attempts are refused the process waits,
bounded, inside itself — woken by the world-change journal or a new approval,
and by a periodic recheck as insurance — and only the ratified classes end
the shift: budget, the wall clock, the cycle cap (named as the shift limit),
consecutive errors. A run WITHOUT a goal picker keeps the old stops: it is
not a shift.

The second half: the approval inbox re-reads its file before every read and
write, so an operator's verdict written from outside reaches the live
process — that evening the grant and the denial both needed the run killed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from core.approval_inbox import ApprovalInbox
from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig
from core.capability_events import record_capability_snapshot

_NOW = datetime(2026, 9, 3, 21, 0, tzinfo=timezone.utc)


def _action(name: str, priority: int = 59):
    return SimpleNamespace(
        action=name, title=name, severity="medium", priority=priority,
        risk="reversible", grounds="operator_goal", decided_by="test",
        next_check_at=None, reason="",
    )


class _Gather:
    def __init__(self, name: str = "propose_engineering_task", idle: bool = False):
        self.name, self.idle = name, idle
        self.goals: list[str] = []

    def __call__(self, agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        self.goals.append(goal)
        if self.idle:
            return {"action": _action("observe", priority=0)}
        return {"action": _action(self.name)}


class _Execute:
    def __init__(self, result: str = "completed", product: bool = False):
        self.result, self.product, self.calls = result, product, 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.calls += 1
        return CampaignActionOutcome(
            result=self.result, llm_calls_spent=1, cost_units_spent=1,
            subject="the-subject", work_done=True,
            artifact="something" if self.product else None,
        )


class _Sleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _run(tmp_path, *, gather, execute, next_goal, max_cycles=9, pause=0,
         sleep=None, inbox=None, wall=0, llm_cap=100, unproductive=0, log=None):
    return run_campaign(
        CampaignConfig(goal="первая цель", max_cycles=max_cycles, max_idle_streak=3,
                       dry_run=False, cycle_pause_seconds=pause,
                       max_wall_clock_seconds=wall, max_llm_calls=llm_cap,
                       max_unproductive_streak=unproductive),
        agent=SimpleNamespace(log=log),
        workspace=str(tmp_path),
        gather_signals=gather, execute_action=execute,
        ledger=CampaignLedger(), next_goal=next_goal,
        now_fn=lambda: _NOW, sleep_fn=sleep or _Sleep(),
        approval_inbox=inbox,
    )


# ── the three evening stops ───────────────────────────────────────────────


def test_idle_with_every_switch_refused_waits_instead_of_stopping(tmp_path) -> None:
    """21:14 that evening: three declined goals → idle_stall → sleep 12h."""
    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(),
                  next_goal=lambda: "")

    assert not result.stop_reason.startswith("idle_stall"), result.stop_reason
    assert result.stop_reason.startswith("shift_limit:")
    waits = [r for r in result.records if r.result == "waiting"]
    assert waits and waits[0].work_done is False
    assert "idle_stall" in waits[0].reason, "the cause of the wait is recorded"


def test_loop_suspected_reselects_or_waits(tmp_path) -> None:
    """20:57 that evening: three honest «inexpressible» verdicts → loop_suspected."""
    goals = iter(["вторая цель", "третья цель", "четвёртая цель"])
    gather = _Gather()
    events: list[str] = []
    result = _run(tmp_path, gather=gather, execute=_Execute(product=False),
                  next_goal=lambda: next(goals, ""), unproductive=3, max_cycles=12,
                  log=SimpleNamespace(log=lambda e, p=None, **k: events.append(e)))

    assert "campaign_loop_suspected" in events, "the sensor still fires and is logged"
    assert not result.stop_reason.startswith("loop_suspected"), result.stop_reason
    assert "вторая цель" in gather.goals, "the suspected loop led to a new goal"


def test_no_progress_with_switch_refused_waits(tmp_path) -> None:
    result = _run(tmp_path, gather=_Gather(), execute=_Execute(),
                  next_goal=lambda: "")

    assert not result.stop_reason.startswith("no_progress_stall"), result.stop_reason
    assert any(r.result == "waiting" for r in result.records)


# ── the wait itself ───────────────────────────────────────────────────────


def test_the_wait_is_paced_and_bounded(tmp_path) -> None:
    sleep = _Sleep()
    _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(),
         next_goal=lambda: "", pause=60, sleep=sleep, max_cycles=4)

    waits = [s for s in sleep.calls if s == 60.0]
    assert waits, "a paced shift sleeps in its own pace"
    assert sum(sleep.calls) <= 900 * 2 + 60 * 4, "the wait is bounded, not a sleep till morning"


def test_a_world_change_wakes_the_wait_early(tmp_path) -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    record_capability_snapshot(tmp_path, frozenset({"a"}))  # baseline row

    class _Sleeper(_Sleep):
        def __call__(self, seconds: float) -> None:
            super().__call__(seconds)
            # calls 1–2 are the pauses before cycles 2 and 3; the backoff of
            # cycle 3 sleeps from call 3 on — change the world on its 2nd step.
            if len(self.calls) == 4:
                record_capability_snapshot(tmp_path, frozenset({"a", "b"}))  # the world changed

    sleep = _Sleeper()
    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(),
                  next_goal=lambda: "", pause=60, sleep=sleep, max_cycles=4)

    waits = [r for r in result.records if r.result == "waiting"]
    assert waits and "woke_by=world_changed" in waits[0].reason, waits[0].reason if waits else "no wait"
    assert len([s for s in sleep.calls if s == 60.0]) < 15, "it slept the whole window anyway"


def test_a_new_approval_wakes_the_wait_early(tmp_path) -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    item = inbox.add(operation="autonomous_runtime.standing_grant", summary="grant",
                     payload={"max_runs_per_day": 5})

    class _Sleeper(_Sleep):
        def __call__(self, seconds: float) -> None:
            super().__call__(seconds)
            if len(self.calls) == 4:  # second step of the backoff (see above)
                # the operator's word, written by ANOTHER inbox object
                ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl").approve(
                    item.id, actor="operator")

    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(),
                  next_goal=lambda: "", pause=60, sleep=_Sleeper(), max_cycles=4,
                  inbox=inbox)

    waits = [r for r in result.records if r.result == "waiting"]
    assert waits and "woke_by=new_approval" in waits[0].reason, waits[0].reason if waits else "no wait"


def test_a_periodic_recheck_ends_the_wait_without_any_event(tmp_path) -> None:
    """Insurance: a missed event must not become a very correct death."""
    sleep = _Sleep()
    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(),
                  next_goal=lambda: "", pause=60, sleep=sleep, max_cycles=4)

    waits = [r for r in result.records if r.result == "waiting"]
    assert waits and "woke_by=periodic_recheck" in waits[0].reason
    assert result.stop_reason.startswith("shift_limit:")


# ── the ratified terminal classes still end the shift ─────────────────────


def test_the_wall_clock_still_ends_the_shift(tmp_path) -> None:
    clock = {"t": 0}

    def _now():
        clock["t"] += 100  # every look at the clock costs 100 s
        return datetime.fromtimestamp(1_756_000_000 + clock["t"], tz=timezone.utc)

    result = run_campaign(
        CampaignConfig(goal="g", max_cycles=50, max_idle_streak=3, dry_run=False,
                       max_wall_clock_seconds=350, max_llm_calls=100),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=_Gather(idle=True), execute_action=_Execute(),
        ledger=CampaignLedger(), next_goal=lambda: "", now_fn=_now, sleep_fn=_Sleep(),
    )
    assert result.stop_reason.startswith("wall_clock_exhausted"), result.stop_reason


def test_the_budget_still_ends_the_shift(tmp_path) -> None:
    result = _run(tmp_path, gather=_Gather(), execute=_Execute(),
                  next_goal=lambda: "", llm_cap=1, max_cycles=20)
    assert result.stop_reason.startswith("budget_exhausted"), result.stop_reason


def test_the_cycle_cap_is_named_as_the_shift_limit(tmp_path) -> None:
    result = _run(tmp_path, gather=_Gather(), execute=_Execute(product=True),
                  next_goal=lambda: "", max_cycles=2)
    assert result.stop_reason == "shift_limit:max_cycles=2"


def test_without_a_goal_picker_the_old_stops_remain(tmp_path) -> None:
    """Control: a run that cannot choose goals is not a shift."""
    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(),
                  next_goal=None)
    assert result.stop_reason.startswith(("idle_stall", "healthy_idle")), result.stop_reason
    assert not any(r.result == "waiting" for r in result.records)


# ── the live inbox ────────────────────────────────────────────────────────


def test_an_outside_verdict_survives_the_live_process_next_save(tmp_path) -> None:
    """That evening's lost-update hazard, reproduced with two inbox objects."""
    path = tmp_path / "inbox.jsonl"
    live = ApprovalInbox(path=path)            # the running process
    item = live.add(operation="self_apply_lane.run", summary="proposal",
                    payload={"files": [{"path": "core/x.py", "content": "a"}]})

    ApprovalInbox(path=path).deny(item.id, reason="operator's word", actor="operator")
    live.add(operation="self_apply_lane.run", summary="another")  # the process saves again

    assert ApprovalInbox(path=path).get(item.id).status == "denied", "the verdict was overwritten"
    assert live.get(item.id).status == "denied", "the live process does not see the verdict"


def test_an_outside_grant_is_seen_by_the_live_process(tmp_path) -> None:
    from core.autonomous_runtime import active_standing_grant

    path = tmp_path / "data" / "approval_inbox.jsonl"
    path.parent.mkdir(parents=True)
    live = ApprovalInbox(path=path)
    assert active_standing_grant(live, tmp_path) is None

    outside = ApprovalInbox(path=path)
    grant = outside.add(operation="autonomous_runtime.standing_grant", summary="40/day",
                        payload={"max_runs_per_day": 40},
                        expires_at="2999-01-01T00:00:00+00:00")
    outside.approve(grant.id, actor="operator")

    found = active_standing_grant(live, tmp_path)
    assert found is not None and found.id == grant.id, "the grant needed a restart to be seen"


def test_an_in_memory_inbox_keeps_working(tmp_path) -> None:
    """Control: path=None inboxes (tests, dry runs) have nothing to re-read."""
    inbox = ApprovalInbox(path=None)
    item = inbox.add(operation="proposed_task", summary="x")
    assert inbox.get(item.id) is not None and len(inbox.pending()) == 1
