"""Tests for the 24/48h autonomous campaign engine (core/campaign.py).

The campaign loop is the layer above a single tick. These tests pin its three
load-bearing guarantees with deterministic fakes (NO LLM, NO network):

  1. an IDLE cycle (observe / priority<=0) never calls execute_action -> never
     spends a model call;
  2. ``max_idle_streak`` consecutive idle cycles stops the campaign;
  3. a budget cap (llm_calls / cost_units) stops the campaign BEFORE the next
     spend, and every cycle lands one honest ledger row.

Collaborators are injected, so the loop is exercised against REAL
BestNextAction objects and REAL CampaignCycleRecord shapes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.best_next_action import BestNextAction
from core.campaign import (
    CampaignActionOutcome,
    CampaignConfig,
    CampaignCycleRecord,
    CampaignLedger,
    CampaignResult,
    _cost_totals,
    run_campaign,
)


# ============================================================
# Helpers
# ============================================================

def _observe() -> BestNextAction:
    return BestNextAction(
        action="observe",
        title="Stay in honest observation",
        severity="none",
        priority=0,
        reason="nothing pressing right now",
        risk="read_only",
    )


def _useful(action: str = "propose_minimal_test_repair", priority: int = 80) -> BestNextAction:
    return BestNextAction(
        action=action,
        title="Propose one minimal fix",
        severity="high",
        priority=priority,
        reason="tests are failing with concrete names",
        risk="reversible",
    )


class _ScriptedGather:
    """Yields a scripted sequence of actions; repeats the last one forever."""

    def __init__(self, actions: list[BestNextAction]):
        self._actions = actions
        self.calls = 0

    def __call__(self, agent, workspace, approval_inbox):
        idx = min(self.calls, len(self._actions) - 1)
        self.calls += 1
        return {"action": self._actions[idx]}


class _RecordingExecute:
    """Fake execute_action that records calls and returns a fixed outcome."""

    def __init__(self, outcome: CampaignActionOutcome):
        self._outcome = outcome
        self.calls = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.calls += 1
        return self._outcome


def _explode_execute(**_kwargs):  # pragma: no cover - must never run in idle tests
    raise AssertionError("execute_action must NOT be called on an idle cycle")


def _fixed_now():
    return datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)


# ============================================================
# Config validation
# ============================================================

class TestCampaignConfig:
    def test_defaults_are_valid(self):
        cfg = CampaignConfig()
        assert cfg.max_cycles == 24
        assert cfg.max_llm_calls == 100
        assert cfg.max_idle_streak == 3
        assert cfg.dry_run is True

    @pytest.mark.parametrize("kwargs", [
        {"max_cycles": 0},
        {"max_idle_streak": 0},
        {"max_llm_calls": -1},
        {"max_cost_units": -1},
        {"report_every": 0},
        {"idle_recheck_seconds": -1},
        {"max_wall_clock_seconds": -1},
        {"cycle_pause_seconds": -1},
        {"max_consecutive_errors": 0},
        {"max_unproductive_streak": -1},
    ])
    def test_invalid_config_rejected(self, kwargs):
        with pytest.raises(ValueError):
            CampaignConfig(**kwargs)


# ============================================================
# Idle cycles never spend the LLM
# ============================================================

class TestIdleNeverSpends:
    def test_idle_cycle_does_not_call_execute(self):
        gather = _ScriptedGather([_observe()])
        result = run_campaign(
            CampaignConfig(max_cycles=2, max_idle_streak=5),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert all(r.idle for r in result.records)
        assert all(r.llm_calls_spent == 0 for r in result.records)
        assert result.totals["llm_calls"] == 0
        assert result.totals["idle_cycles"] == 2
        assert result.totals["useful_cycles"] == 0

    def test_idle_record_carries_reason_and_next_check(self):
        gather = _ScriptedGather([_observe()])
        result = run_campaign(
            CampaignConfig(max_cycles=1, max_idle_streak=5, idle_recheck_seconds=600),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        rec = result.records[0]
        assert rec.result == "idle"
        assert rec.reason == "nothing pressing right now"
        assert rec.next_check_at == "2026-06-05T12:10:00+00:00"


# ============================================================
# Idle-stall stop
# ============================================================

class TestIdleStall:
    def test_three_idle_in_a_row_stops_campaign(self):
        gather = _ScriptedGather([_observe()])
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_idle_streak=3),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "stopped"
        assert result.stop_reason.startswith("idle_stall:3")
        assert result.cycles_run == 3

    def test_useful_cycle_resets_idle_streak(self):
        # idle, useful, idle, idle, idle -> stop at the 3rd consecutive idle.
        gather = _ScriptedGather([
            _observe(), _useful(), _observe(), _observe(), _observe(),
        ])
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=1,
                                  cost_units_spent=3, proposal="approvals_pending=1")
        )
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_idle_streak=3, max_llm_calls=0),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "stopped"
        assert result.stop_reason.startswith("idle_stall:3")
        assert result.cycles_run == 5
        assert result.totals["useful_cycles"] == 1
        assert result.totals["idle_cycles"] == 4
        assert result.totals["proposals"] == 1
        assert execute.calls == 1


# ============================================================
# Budget stop
# ============================================================

class TestBudgetStop:
    def test_llm_calls_budget_stops_before_next_spend(self):
        # Distinct actions so each cycle executes and spends (a repeated action
        # would be deduped and never reach the budget cap).
        gather = _ScriptedGather([_useful("fix_a"), _useful("fix_b")])
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=1)
        )
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_llm_calls=2),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "stopped"
        assert result.stop_reason.startswith("budget_exhausted:llm_calls")
        assert result.cycles_run == 2
        assert result.totals["llm_calls"] == 2
        assert execute.calls == 2

    def test_cost_units_budget_stops(self):
        gather = _ScriptedGather([_useful("fix_a"), _useful("fix_b")])
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=1, cost_units_spent=3)
        )
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_llm_calls=0, max_cost_units=5),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        # cycle1 cost 0<5 -> spend 3; cycle2 cost 3<5 -> spend 3 (=6); cycle3 6>=5 stop.
        assert result.stop_reason.startswith("budget_exhausted:cost_units")
        assert result.cycles_run == 2
        assert result.totals["cost_units"] == 6


# ============================================================
# Completed run
# ============================================================

class TestCompletedRun:
    def test_runs_all_cycles_when_nothing_trips(self):
        # Distinct actions so each cycle is a genuinely-new useful pass.
        gather = _ScriptedGather([_useful("fix_a"), _useful("fix_b"), _useful("fix_c")])
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=0)
        )
        result = run_campaign(
            CampaignConfig(max_cycles=3, max_llm_calls=0),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "completed"
        assert result.stop_reason == ""
        assert result.cycles_run == 3
        assert result.totals["useful_cycles"] == 3


# ============================================================
# Signal dedup (B) — a repeated action does NOT re-execute
# ============================================================

class TestRepeatDedup:
    def test_repeated_action_skips_execution_and_stalls(self):
        # The same signal forever: cycle1 executes (new), then every re-pick is
        # a REPEAT (no LLM, no execution) and counts toward the no-progress
        # stall — proving the campaign does NOT spin on one signal.
        gather = _ScriptedGather([_useful("restore_daemon_liveness")])
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=2, cost_units_spent=6)
        )
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_idle_streak=3, max_llm_calls=0),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        # cycle1 new->exec, cycles 2,3,4 repeat -> streak 3 -> stop.
        assert execute.calls == 1
        assert result.status == "stopped"
        assert result.stop_reason.startswith("no_progress_stall:3")
        assert result.cycles_run == 4
        assert result.totals["useful_cycles"] == 1
        assert result.totals["repeat_cycles"] == 3
        # Only the single real execution spent anything.
        assert result.totals["llm_calls"] == 2
        assert result.totals["cost_units"] == 6

    def test_repeat_record_is_honest_and_logged(self):
        events: list[tuple[str, dict]] = []
        agent = SimpleNamespace(log=SimpleNamespace(log=lambda e, p: events.append((e, p))))
        gather = _ScriptedGather([_useful("review_dry_run_stall")])
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_idle_streak=2, max_llm_calls=0),
            agent=agent,
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        # cycle1 new->exec, cycle2 repeat (streak1), cycle3 repeat (streak2)->stop.
        assert result.cycles_run == 3
        repeat = result.records[1]
        assert repeat.result == "repeat"
        assert repeat.idle is False
        assert repeat.llm_calls_spent == 0
        assert repeat.cost_units_spent == 0
        assert "already attempted" in repeat.reason
        assert "REPEAT (no LLM, skipped)" in repeat.user_summary()
        names = [e for e, _ in events]
        assert "campaign_cycle_repeat" in names

    def test_new_action_resets_the_no_progress_streak(self):
        # a, a(repeat), b(new->reset), b(repeat), b(repeat) -> stall on b.
        gather = _ScriptedGather([
            _useful("fix_a"), _useful("fix_a"),
            _useful("fix_b"), _useful("fix_b"), _useful("fix_b"),
        ])
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_idle_streak=3, max_llm_calls=0),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        # fix_a executes (cycle1), repeat (cycle2, streak1); fix_b is NEW -> reset
        # + execute (cycle3, streak0); repeat (cycle4, streak1), repeat (cycle5,
        # streak2). streak never reaches 3 -> completed at max_cycles? No: only
        # 5 scripted then it repeats fix_b forever -> cycle6 repeat streak3 stop.
        assert execute.calls == 2  # fix_a once + fix_b once
        assert result.totals["useful_cycles"] == 2
        assert result.stop_reason.startswith("no_progress_stall:3")


# ============================================================
# Ledger persistence
# ============================================================

class TestCampaignLedger:
    def test_records_are_appended_to_disk(self, tmp_path: Path):
        path = tmp_path / "data" / "campaign_ledger.jsonl"
        ledger = CampaignLedger(path=path)
        gather = _ScriptedGather([_useful(), _observe(), _observe(), _observe()])
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=1, artifact="reports/x.md")
        )
        result = run_campaign(
            CampaignConfig(max_cycles=10, max_idle_streak=3, max_llm_calls=0),
            agent=SimpleNamespace(log=None),
            workspace=str(tmp_path),
            gather_signals=gather,
            execute_action=execute,
            ledger=ledger,
            now_fn=_fixed_now,
        )
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == result.cycles_run
        first = json.loads(lines[0])
        assert first["idle"] is False
        assert first["action"] == "propose_minimal_test_repair"
        assert first["artifact"] == "reports/x.md"
        assert json.loads(lines[1])["idle"] is True

    def test_ledger_without_path_keeps_in_memory(self):
        ledger = CampaignLedger()
        rec = CampaignCycleRecord(
            cycle=1, ts="t", goal="g", action="observe", action_title="t",
            severity="none", priority=0, risk="read_only", idle=True,
            llm_calls_spent=0, cost_units_spent=0, result="idle", reason="r",
        )
        ledger.append(rec)
        assert ledger.records == [rec]
        assert ledger.path is None


class TestLedgerReader:
    def test_load_rows_missing_file_is_empty(self, tmp_path: Path):
        from core.campaign import load_ledger_rows

        assert load_ledger_rows(tmp_path / "nope.jsonl") == []

    def test_load_rows_skips_blank_and_malformed_lines(self, tmp_path: Path):
        from core.campaign import load_ledger_rows

        path = tmp_path / "ledger.jsonl"
        path.write_text(
            '{"cycle": 1, "action": "a"}\n'
            "\n"
            "not json at all\n"
            '"a bare string is not a dict"\n'
            '{"cycle": 2, "action": "b"}\n',
            encoding="utf-8",
        )
        rows = load_ledger_rows(path)
        assert [r["cycle"] for r in rows] == [1, 2]

    def test_summarise_empty_rows(self):
        from core.campaign import summarise_ledger

        text = summarise_ledger([])
        assert "campaign ledger" in text
        assert "empty" in text

    def test_summarise_aggregates_and_shows_recent(self):
        from core.campaign import summarise_ledger

        rows = [
            {"cycle": 1, "goal": "G1", "action": "restore_daemon_liveness",
             "idle": False, "result": "completed", "llm_calls_spent": 2,
             "cost_units_spent": 6, "artifact": "reasoning: x", "reason": ""},
            {"cycle": 2, "goal": "G1", "action": "restore_daemon_liveness",
             "idle": False, "result": "repeat", "llm_calls_spent": 0,
             "cost_units_spent": 0, "reason": "already attempted"},
            {"cycle": 3, "goal": "G2", "action": "observe",
             "idle": True, "result": "idle", "llm_calls_spent": 0,
             "cost_units_spent": 0, "reason": "nothing pressing"},
        ]
        text = summarise_ledger(rows, recent=2)
        # All-time aggregates over every row.
        assert "cycles_logged=3" in text
        assert "useful=1" in text
        assert "idle=1" in text
        assert "repeats=1" in text
        assert "llm_calls=2" in text
        assert "cost_units=6" in text
        assert "artifacts=1" in text
        # Per-result breakdown + distinct goals.
        assert "completed=1" in text
        assert "repeat=1" in text
        assert "idle=1" in text
        assert "G1" in text and "G2" in text
        # recent=2 shows only the last two cycles (cycle 1 omitted from tail).
        assert "recent 2 cycle(s):" in text
        assert "REPEAT (no LLM, skipped)" in text
        assert "[cycle 3] IDLE (no LLM)" in text
        assert "[cycle 1]" not in text

    def test_summarise_recent_zero_shows_all(self):
        from core.campaign import summarise_ledger

        rows = [
            {"cycle": i, "goal": "G", "action": "observe", "idle": True,
             "result": "idle", "llm_calls_spent": 0, "cost_units_spent": 0,
             "reason": "r"}
            for i in range(1, 4)
        ]
        text = summarise_ledger(rows, recent=0)
        assert "recent 3 cycle(s):" in text
        assert "[cycle 1]" in text and "[cycle 3]" in text


# ============================================================
# Reporting + cost probe
# ============================================================

class TestReporting:
    def test_result_to_dict_and_user_summary(self):
        gather = _ScriptedGather([_observe()])
        result = run_campaign(
            CampaignConfig(max_cycles=1, max_idle_streak=5),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        payload = result.to_dict()
        assert payload["status"] == "completed"
        assert payload["records"][0]["result"] == "idle"
        summary = result.user_summary()
        assert "autonomous campaign" in summary
        assert "IDLE (no LLM)" in summary

    def test_log_events_are_emitted(self):
        events: list[tuple[str, dict]] = []
        agent = SimpleNamespace(log=SimpleNamespace(log=lambda e, p: events.append((e, p))))
        gather = _ScriptedGather([_observe()])
        run_campaign(
            CampaignConfig(max_cycles=1, max_idle_streak=5),
            agent=agent,
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        names = [e for e, _ in events]
        assert "campaign_start" in names
        assert "campaign_cycle_idle" in names
        assert "campaign_stop" in names


class TestCycleRecordSummary:
    def test_useful_record_summary_lists_cost_and_refs(self):
        rec = CampaignCycleRecord(
            cycle=2, ts="t", goal="g", action="propose_minimal_test_repair",
            action_title="t", severity="high", priority=80, risk="reversible",
            idle=False, llm_calls_spent=1, cost_units_spent=3, result="completed",
            reason="r", proposal="approvals_pending=1", artifact="reports/x.md",
        )
        text = rec.user_summary()
        assert "completed" in text
        assert "llm=1" in text
        assert "proposal=approvals_pending=1" in text
        assert "artifact=reports/x.md" in text


class TestDefaultGatherSignals:
    def test_gather_on_empty_workspace_returns_observe_action(self, tmp_path: Path):
        from core.campaign import _default_gather_signals

        signals = _default_gather_signals(SimpleNamespace(log=None), str(tmp_path), None)
        action = signals["action"]
        assert isinstance(action, BestNextAction)
        # An empty workspace has no heartbeat -> the daemon-liveness candidate wins.
        assert action.action == "restore_daemon_liveness"
        assert "triage" in signals


class TestActionFocusedGoal:
    def test_goal_is_coupled_to_the_picked_action(self):
        from core.campaign import _action_focused_goal

        action = _useful(action="restore_daemon_liveness")
        text = _action_focused_goal("Check agent health", action)
        # Action-COUPLED: the campaign goal AND the picked signal both appear.
        assert "Check agent health" in text
        assert "restore_daemon_liveness" in text
        assert action.title in text
        assert action.reason in text
        # Read-only contract is stated to the model.
        assert "read-only" in text.lower()

    def test_evidence_is_included_when_present(self):
        from core.campaign import _action_focused_goal

        action = BestNextAction(
            action="investigate_tick_error",
            title="Look into the tick error",
            severity="critical",
            priority=90,
            reason="last tick failed",
            risk="read_only",
            evidence=("error in logs/agent_tick.log", "exit code 1"),
        )
        text = _action_focused_goal("Stabilise the daemon", action)
        assert "error in logs/agent_tick.log" in text
        assert "exit code 1" in text

    def test_execute_action_couples_goal_and_surfaces_reasoning(self, monkeypatch):
        """`_default_execute_action` must build an action-coupled goal and turn
        the goal task's answer into a readable artifact in the ledger."""
        import core.autonomous_runtime as ar
        from core.campaign import _default_execute_action

        captured: dict = {}

        class _FakeRuntime:
            def __init__(self, agent, *, workspace, approval_inbox=None):
                pass

            def run(self, config):
                captured["goal"] = config.goal
                captured["include_goal"] = config.include_goal
                captured["dry_run"] = config.dry_run
                goal_task = SimpleNamespace(
                    task=SimpleNamespace(kind="goal"),
                    details={"answer": "Re-run agent_tick.py --status to confirm liveness."},
                )
                return SimpleNamespace(
                    status="completed",
                    approvals={"pending": 0},
                    tasks=[goal_task],
                )

        monkeypatch.setattr(ar, "AutonomousRuntime", _FakeRuntime)

        budget_ledger = SimpleNamespace(
            snapshot=lambda: {"totals": {"llm_calls": 0, "model_cost_units": 0}}
        )
        agent = SimpleNamespace(
            model_router=SimpleNamespace(
                usage_ledger=SimpleNamespace(budget_ledger=budget_ledger)
            )
        )
        config = CampaignConfig(goal="Verify agent state", dry_run=True)
        action = _useful(action="restore_daemon_liveness")

        outcome = _default_execute_action(
            agent=agent, workspace="/ws", action=action, config=config
        )

        assert captured["include_goal"] is True
        assert captured["dry_run"] is True
        assert "Verify agent state" in captured["goal"]
        assert "restore_daemon_liveness" in captured["goal"]
        assert outcome.result == "completed"
        assert outcome.artifact == "reasoning: Re-run agent_tick.py --status to confirm liveness."


class TestCostTotals:
    def test_absent_ledger_chain_returns_zeros(self):
        assert _cost_totals(SimpleNamespace(model_router=SimpleNamespace())) == (0, 0)
        assert _cost_totals(SimpleNamespace()) == (0, 0)

    def test_reads_totals_from_budget_ledger(self):
        budget_ledger = SimpleNamespace(
            snapshot=lambda: {"totals": {"llm_calls": 7, "model_cost_units": 21}}
        )
        agent = SimpleNamespace(
            model_router=SimpleNamespace(
                usage_ledger=SimpleNamespace(budget_ledger=budget_ledger)
            )
        )
        assert _cost_totals(agent) == (7, 21)


# ============================================================
# Real wall-clock pacing + budget (step 3: campaign over real time)
# ============================================================

class _FakeClock:
    """Deterministic clock that only advances when sleep() is called.

    Models real wall-clock honestly: time passes when (and only when) the
    campaign actually sleeps between cycles, so tests stay instant while
    exercising the exact now_fn/sleep_fn wiring used in production.
    """

    def __init__(self, start: datetime | None = None):
        self._t = start or datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t = self._t + timedelta(seconds=seconds)


def _distinct_useful(n: int) -> list[BestNextAction]:
    # Distinct action names so every cycle is a genuinely NEW useful action
    # (avoids the repeat-dedup path), letting us count paced cycles cleanly.
    return [_useful(action=f"fix_{i}", priority=80) for i in range(n)]


class TestWallClockPacing:
    def test_pause_happens_between_cycles_never_before_first(self):
        clock = _FakeClock()
        gather = _ScriptedGather(_distinct_useful(3))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(
                max_cycles=3, max_idle_streak=9, cycle_pause_seconds=5
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=clock.now,
            sleep_fn=clock.sleep,
        )
        assert result.cycles_run == 3
        # 3 cycles -> paced before cycles 2 and 3 only (never before the first).
        assert clock.sleeps == [5.0, 5.0]
        assert result.totals["wall_clock_seconds"] == 10.0

    def test_default_is_back_to_back_no_sleep(self):
        clock = _FakeClock()
        gather = _ScriptedGather(_distinct_useful(3))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        run_campaign(
            CampaignConfig(max_cycles=3, max_idle_streak=9),  # pause defaults to 0
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=clock.now,
            sleep_fn=clock.sleep,
        )
        assert clock.sleeps == []

    def test_wall_clock_budget_stops_before_next_work(self):
        clock = _FakeClock()
        gather = _ScriptedGather(_distinct_useful(10))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(
                max_cycles=10,
                max_idle_streak=99,
                cycle_pause_seconds=5,
                max_wall_clock_seconds=10,
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=clock.now,
            sleep_fn=clock.sleep,
        )
        # cycle1@0 runs; pause->5 cycle2@5 runs; pause->10 cycle3@10 == ceiling -> stop
        assert result.status == "stopped"
        assert result.stop_reason.startswith("wall_clock_exhausted")
        assert result.cycles_run == 2
        assert execute.calls == 2

    def test_pause_is_capped_to_remaining_wall_clock(self):
        clock = _FakeClock()
        gather = _ScriptedGather(_distinct_useful(10))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(
                max_cycles=10,
                max_idle_streak=99,
                cycle_pause_seconds=5,
                max_wall_clock_seconds=8,
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=clock.now,
            sleep_fn=clock.sleep,
        )
        # cycle2 sleeps full 5 (remaining 8); cycle3 capped to remaining 3 then stop
        assert clock.sleeps == [5.0, 3.0]
        assert result.cycles_run == 2
        assert result.stop_reason.startswith("wall_clock_exhausted")


# ============================================================
# on_cycle liveness seam (step 3b: daemon heartbeat per cycle)
# ============================================================

class TestOnCycleHook:
    def test_hook_fires_once_per_recorded_cycle(self):
        seen: list[dict] = []
        gather = _ScriptedGather(_distinct_useful(3))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(max_cycles=3, max_idle_streak=9),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
            on_cycle=seen.append,
        )
        assert len(seen) == result.cycles_run == 3
        assert [s["cycle"] for s in seen] == [1, 2, 3]
        assert all(s["result"] == "completed" for s in seen)
        # Running totals are exposed and monotonic for liveness reporting.
        assert [s["useful_cycles"] for s in seen] == [1, 2, 3]

    def test_hook_fires_on_idle_cycles_too(self):
        seen: list[dict] = []
        gather = _ScriptedGather([_observe()])
        run_campaign(
            CampaignConfig(max_cycles=5, max_idle_streak=2),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
            on_cycle=seen.append,
        )
        # idle-stall stops after 2 idle cycles; the hook saw both.
        assert len(seen) == 2
        assert all(s["idle"] and s["result"] == "idle" for s in seen)

    def test_default_no_hook_is_a_noop(self):
        gather = _ScriptedGather(_distinct_useful(2))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        # No on_cycle passed -> must not raise and must still run normally.
        result = run_campaign(
            CampaignConfig(max_cycles=2, max_idle_streak=9),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.cycles_run == 2


# ============================================================
# Per-cycle resilience (a single cycle error must not kill a 48h run)
# ============================================================

class _FlakyGather:
    """Gather that raises on chosen 1-based call indices, else a NEW useful action.

    Distinct action names per call keep every non-error cycle on the genuinely
    NEW path (no repeat dedup), so error vs useful counts stay unambiguous.
    """

    def __init__(self, raise_on: set[int], exc: BaseException | None = None):
        self.raise_on = raise_on
        self.exc = exc or RuntimeError("transient blip")
        self.calls = 0

    def __call__(self, agent, workspace, approval_inbox):
        self.calls += 1
        if self.calls in self.raise_on:
            raise self.exc
        return {"action": _useful(action=f"fix_{self.calls}", priority=80)}


class _BrokenLedger(CampaignLedger):
    """Ledger whose append always fails — models a full disk / locked file."""

    def append(self, record):  # type: ignore[override]
        raise OSError("disk full")


class TestPerCycleResilience:
    def test_single_cycle_error_does_not_end_the_campaign(self):
        # Cycle 2 raises; cycles 1, 3, 4 are normal useful cycles.
        gather = _FlakyGather(raise_on={2})
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(max_cycles=4, max_idle_streak=99, max_consecutive_errors=3),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        # The run survived the transient error and used all 4 cycles.
        assert result.status == "completed"
        assert result.cycles_run == 4
        assert result.totals["error_cycles"] == 1
        assert result.totals["useful_cycles"] == 3
        # The error cycle is recorded honestly in the ledger.
        error_rows = [r for r in result.records if r.result == "error"]
        assert len(error_rows) == 1
        assert error_rows[0].action == "<cycle_error>"
        assert "RuntimeError" in error_rows[0].reason

    def test_consecutive_errors_stop_the_campaign(self):
        # Every cycle raises -> the streak reaches the cap and stops honestly.
        gather = _FlakyGather(raise_on=set(range(1, 100)))
        result = run_campaign(
            CampaignConfig(max_cycles=20, max_idle_streak=99, max_consecutive_errors=3),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "stopped"
        assert result.stop_reason == "error_stall:3_consecutive_cycle_errors"
        assert result.cycles_run == 3
        assert result.totals["error_cycles"] == 3

    def test_a_good_cycle_resets_the_error_streak(self):
        # err, err, good, err, err, good -> never 3 in a row -> survives all 6.
        gather = _FlakyGather(raise_on={1, 2, 4, 5})
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(max_cycles=6, max_idle_streak=99, max_consecutive_errors=3),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "completed"
        assert result.cycles_run == 6
        assert result.totals["error_cycles"] == 4
        assert result.totals["useful_cycles"] == 2

    def test_error_cycles_surface_in_the_on_cycle_snapshot(self):
        seen: list[dict] = []
        gather = _FlakyGather(raise_on={2})
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        run_campaign(
            CampaignConfig(max_cycles=3, max_idle_streak=99, max_consecutive_errors=3),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
            on_cycle=seen.append,
        )
        # The hook fired for every cycle including the error one.
        assert [s["result"] for s in seen] == ["completed", "error", "completed"]
        assert [s["error_cycles"] for s in seen] == [0, 1, 1]

    def test_broken_audit_sink_during_error_does_not_crash(self):
        # The error handler's ledger write fails too; it must be swallowed so a
        # broken sink can't escalate one cycle error into a campaign crash.
        gather = _FlakyGather(raise_on={1})
        result = run_campaign(
            CampaignConfig(max_cycles=1, max_idle_streak=99, max_consecutive_errors=3),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=_explode_execute,
            ledger=_BrokenLedger(),
            now_fn=_fixed_now,
        )
        # No exception propagated; the error cycle is still in the in-memory log.
        assert result.totals["error_cycles"] == 1
        assert result.cycles_run == 1

    def test_keyboard_interrupt_is_not_swallowed(self):
        # An operator interrupt (BaseException) must stop the run immediately,
        # never be caught by the per-cycle resilience seam.
        gather = _FlakyGather(raise_on={1}, exc=KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            run_campaign(
                CampaignConfig(max_cycles=5, max_idle_streak=99),
                agent=SimpleNamespace(log=None),
                workspace="/tmp/ws",
                gather_signals=gather,
                execute_action=_explode_execute,
                ledger=CampaignLedger(),
                now_fn=_fixed_now,
            )


# ============================================================
# loop_suspected — the general "usefulness" brake
# (movement without progress: executed but produced nothing useful)
# ============================================================

class _NewActionGather:
    """Yields a genuinely-NEW useful action every call (distinct names).

    Every cycle reaches the execute path (never the repeat-dedup path), so the
    loop_suspected brake — not no_progress_stall — is what we exercise.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, agent, workspace, approval_inbox):
        self.calls += 1
        return {"action": _useful(action=f"fix_{self.calls}", priority=80)}


class TestLoopSuspected:
    def test_three_unproductive_cycles_trip_loop_suspected(self):
        # Each cycle executes a NEW action but produces no artifact/proposal and
        # the same result_status -> movement without progress -> loop_suspected.
        gather = _NewActionGather()
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(
                max_cycles=20, max_idle_streak=99, max_unproductive_streak=3
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "stopped"
        assert result.stop_reason == "loop_suspected:3_cycles_without_useful_change"
        assert result.cycles_run == 3
        assert result.totals["unproductive_cycles"] == 3

    def test_a_new_proposal_resets_the_unproductive_streak(self):
        # unproductive, unproductive, PROPOSAL (reset), unproductive, unproductive
        # -> never 3 in a row -> survives to max_cycles.
        outcomes = [
            CampaignActionOutcome(result="completed"),
            CampaignActionOutcome(result="completed"),
            CampaignActionOutcome(result="completed", proposal="fix tests"),
            CampaignActionOutcome(result="completed"),
            CampaignActionOutcome(result="completed"),
        ]

        class _SeqExecute:
            def __init__(self, seq): self.seq = seq; self.calls = 0
            def __call__(self, **_kw):
                o = self.seq[min(self.calls, len(self.seq) - 1)]
                self.calls += 1
                return o

        result = run_campaign(
            CampaignConfig(
                max_cycles=5, max_idle_streak=99, max_unproductive_streak=3
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=_NewActionGather(),
            execute_action=_SeqExecute(outcomes),
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "completed"
        assert result.cycles_run == 5
        assert result.totals["proposals"] == 1

    def test_a_new_artifact_resets_the_unproductive_streak(self):
        outcomes = [
            CampaignActionOutcome(result="completed"),
            CampaignActionOutcome(result="completed"),
            CampaignActionOutcome(result="completed", artifact="patch.diff"),
            CampaignActionOutcome(result="completed"),
            CampaignActionOutcome(result="completed"),
        ]

        class _SeqExecute:
            def __init__(self, seq): self.seq = seq; self.calls = 0
            def __call__(self, **_kw):
                o = self.seq[min(self.calls, len(self.seq) - 1)]
                self.calls += 1
                return o

        result = run_campaign(
            CampaignConfig(
                max_cycles=5, max_idle_streak=99, max_unproductive_streak=3
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=_NewActionGather(),
            execute_action=_SeqExecute(outcomes),
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "completed"
        assert result.cycles_run == 5
        assert result.totals["artifacts"] == 1

    def test_loop_suspected_logs_a_report_for_the_operator(self):
        events: list[tuple[str, dict]] = []
        agent = SimpleNamespace(log=SimpleNamespace(log=lambda e, p: events.append((e, p))))
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        run_campaign(
            CampaignConfig(
                max_cycles=20, max_idle_streak=99, max_unproductive_streak=3
            ),
            agent=agent,
            workspace="/tmp/ws",
            gather_signals=_NewActionGather(),
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        suspected = [p for e, p in events if e == "campaign_loop_suspected"]
        assert len(suspected) == 1
        report = suspected[0]
        assert report["cycles_without_progress"] == 3
        assert report["recommended_action"] == "ask_operator_or_change_strategy"
        assert report["useful_state_change"] is False
        # The report names the recent actions so the operator sees the loop.
        assert report["recent_actions"] == ["fix_1", "fix_2", "fix_3"]

    def test_budget_stop_takes_priority_over_loop_suspected(self):
        # Both brakes are armed; the budget cap is checked at the TOP of the
        # loop (before the next spend), so it must win even when the cycles are
        # also unproductive.
        gather = _NewActionGather()
        execute = _RecordingExecute(
            CampaignActionOutcome(result="completed", llm_calls_spent=1)
        )
        result = run_campaign(
            CampaignConfig(
                max_cycles=20,
                max_idle_streak=99,
                max_unproductive_streak=3,
                max_llm_calls=2,
            ),
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        # cycle1 spend=1, cycle2 spend=2; cycle3 budget pre-check stops BEFORE
        # the unproductive streak (which is only 2 at that point) can reach 3.
        assert result.status == "stopped"
        assert result.stop_reason.startswith("budget_exhausted:llm_calls")
        assert result.cycles_run == 2

    def test_off_by_default_never_trips(self):
        # Default config (max_unproductive_streak=0) must never stop on
        # loop_suspected — backward compatible with the pure loop.
        gather = _NewActionGather()
        execute = _RecordingExecute(CampaignActionOutcome(result="completed"))
        result = run_campaign(
            CampaignConfig(max_cycles=6, max_idle_streak=99),  # streak defaults 0=off
            agent=SimpleNamespace(log=None),
            workspace="/tmp/ws",
            gather_signals=gather,
            execute_action=execute,
            ledger=CampaignLedger(),
            now_fn=_fixed_now,
        )
        assert result.status == "completed"
        assert result.cycles_run == 6
        assert result.totals["unproductive_cycles"] >= 3

