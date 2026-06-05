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
from datetime import datetime, timezone
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
        gather = _ScriptedGather([_useful()])
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
        gather = _ScriptedGather([_useful()])
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
        gather = _ScriptedGather([_useful()])
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
