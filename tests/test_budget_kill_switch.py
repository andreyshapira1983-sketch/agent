"""Tests for the TD-022 persistent budget kill-switch.

Covers the pure day-budget evaluation (conservative-default / budget-on-by-
default behaviour), state persistence across a fresh helper load, and the
``agent_tick`` daemon gate that skips all LLM-heavy work — no planner /
synthesizer / provider call — when the switch is engaged.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agent_tick
from core.budget_kill_switch import (
    CONSERVATIVE_DAY_LIMITS,
    REASON_ENGAGED,
    BudgetKillSwitch,
    KillSwitchState,
    default_path,
    evaluate_day_budget,
)
from core.budget_ledger import BudgetLedger
from core.task_queue import TaskQueueStore


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("AGENT_BUDGET_"):
            monkeypatch.delenv(name, raising=False)
    # run_tick does os.environ.setdefault("AGENT_TEST_TIMEOUT_SECONDS", ...);
    # pin it via monkeypatch so the global mutation is reverted on teardown and
    # cannot leak into tests that assert the default RunTestsTool timeout.
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "300")


def _day_snapshot(counters: dict[str, dict[str, int]]) -> dict:
    return {
        "windows": [
            {"name": "hour", "seconds": 3600, "counters": {}},
            {"name": "day", "seconds": 86400, "counters": counters},
        ],
    }


# ── pure evaluation ─────────────────────────────────────────────────────────


def test_missing_config_is_not_unlimited_for_autonomous_mode():
    """All-zero / missing day limits must NOT behave as unlimited: usage above
    the conservative default trips the switch."""
    over = CONSERVATIVE_DAY_LIMITS["llm_calls"] + 1
    state = evaluate_day_budget(_day_snapshot({"llm_calls": {"used": over, "limit": 0}}))

    assert state.active is True
    assert state.counter == "llm_calls"
    assert state.window == "day"
    assert state.limit == CONSERVATIVE_DAY_LIMITS["llm_calls"]
    assert state.limit_source == "conservative_default"
    assert state.reason == REASON_ENGAGED


def test_usage_below_conservative_default_stays_inactive():
    state = evaluate_day_budget(_day_snapshot({"llm_calls": {"used": 1, "limit": 0}}))
    assert state.active is False


def test_configured_limit_takes_precedence_over_default():
    # Configured day limit of 5, used 5 -> tripped, sourced from config.
    state = evaluate_day_budget(_day_snapshot({"llm_calls": {"used": 5, "limit": 5}}))
    assert state.active is True
    assert state.limit == 5
    assert state.limit_source == "config"


def test_status_payload_includes_operator_fields():
    over = CONSERVATIVE_DAY_LIMITS["model_tokens"] + 10
    state = evaluate_day_budget(
        _day_snapshot({"model_tokens": {"used": over, "limit": 0}})
    )
    payload = state.to_dict()
    for key in ("active", "reason", "counter", "window", "used", "limit", "timestamp"):
        assert key in payload
    assert payload["counter"] == "model_tokens"
    assert payload["used"] == over


# ── persistence / latching ──────────────────────────────────────────────────


def test_kill_switch_persists_across_fresh_load(tmp_path: Path):
    path = tmp_path / "budget_kill_switch.json"
    over = CONSERVATIVE_DAY_LIMITS["llm_calls"] + 1
    snapshot = _day_snapshot({"llm_calls": {"used": over, "limit": 0}})

    engaged = BudgetKillSwitch(path=path).engage_if_needed(snapshot)
    assert engaged.active is True
    assert path.exists()

    # A brand-new helper instance (simulating a new process) sees the latch,
    # even against a now-empty snapshot.
    reloaded = BudgetKillSwitch(path=path).load()
    assert reloaded.active is True
    assert reloaded.counter == "llm_calls"

    # And the daemon gate keeps returning active regardless of live usage.
    still = BudgetKillSwitch(path=path).engage_if_needed(_day_snapshot({}))
    assert still.active is True


def test_clear_resets_latched_state(tmp_path: Path):
    path = tmp_path / "budget_kill_switch.json"
    over = CONSERVATIVE_DAY_LIMITS["llm_calls"] + 1
    ks = BudgetKillSwitch(path=path)
    ks.engage_if_needed(_day_snapshot({"llm_calls": {"used": over, "limit": 0}}))
    assert path.exists()

    ks.clear()
    assert not path.exists()
    assert BudgetKillSwitch(path=path).load().active is False


def test_status_is_read_only_when_inactive(tmp_path: Path):
    path = tmp_path / "budget_kill_switch.json"
    ks = BudgetKillSwitch(path=path)
    state = ks.status(_day_snapshot({"llm_calls": {"used": 1, "limit": 0}}))
    assert state.active is False
    assert not path.exists()  # status must never latch


def test_corrupt_state_file_loads_inactive(tmp_path: Path):
    path = tmp_path / "budget_kill_switch.json"
    path.write_text("{not json", encoding="utf-8")
    assert BudgetKillSwitch(path=path).load().active is False


def test_state_from_dict_roundtrip():
    state = KillSwitchState(
        active=True, reason=REASON_ENGAGED, counter="llm_calls",
        window="day", used=101, limit=100, limit_source="config",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    assert KillSwitchState.from_dict(state.to_dict()) == state


# ── daemon gate (agent_tick.run_tick) ───────────────────────────────────────


def _record_day_usage(workspace: Path, counter: str, amount: int, limit: int) -> None:
    """Configure a small day limit and record usage that meets it."""
    (workspace / "config").mkdir(parents=True, exist_ok=True)
    (workspace / "config" / "budget_limits.json").write_text(
        json.dumps({"windows": {"day": {counter: limit}}}),
        encoding="utf-8",
    )
    ledger = BudgetLedger.from_env(
        path=workspace / "data" / "budget_ledger.jsonl",
        config_path=workspace / "config" / "budget_limits.json",
    )
    ledger.record(counter, amount=amount, reason="test",
                  now=datetime.now(timezone.utc))


def test_daemon_skips_llm_work_when_day_budget_exhausted(tmp_path: Path, monkeypatch):
    workspace = tmp_path
    _record_day_usage(workspace, "llm_calls", amount=2, limit=1)

    # A pending task exists; the daemon must NOT build an agent or process it.
    queue = TaskQueueStore(workspace / "data" / "task_queue.jsonl")
    queue.add(goal="anything", dry_run=True, include_tests=False, limit=1)

    import main

    def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("build_agent must not run when kill-switch active")

    monkeypatch.setattr(main, "build_agent", _boom)

    exit_code = agent_tick.run_tick(workspace, dry_run=True)
    assert exit_code == 0

    # Task stays pending — no planner/synthesizer/tool ran.
    assert queue.list(status="pending"), "task must remain unprocessed"

    # Heartbeat + tick log record the kill-switch reason.
    heartbeat = json.loads(
        (workspace / "data" / "daemon_heartbeat.json").read_text(encoding="utf-8")
    )
    assert heartbeat["event"] == "budget_kill_switch"
    assert heartbeat["reason"] == REASON_ENGAGED
    assert heartbeat["counter"] == "llm_calls"
    assert heartbeat["window"] == "day"
    assert heartbeat["used"] >= heartbeat["limit"] > 0

    events = [
        json.loads(line)
        for line in (workspace / "logs" / "daemon_tick.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    kill_events = [e for e in events if e.get("event") == "budget_kill_switch"]
    assert kill_events, "expected a budget_kill_switch tick event"
    # The daemon short-circuits before the scheduler tick.
    assert not any(e.get("event") == "scheduler_tick" for e in events)


def test_daemon_runs_normally_when_budget_available(tmp_path: Path):
    """Control: with no exhausted budget the kill-switch stays inactive and the
    daemon proceeds through its normal (no-pending-task) path."""
    workspace = tmp_path
    exit_code = agent_tick.run_tick(workspace, dry_run=True)
    assert exit_code == 0

    state = BudgetKillSwitch(path=default_path(workspace)).load()
    assert state.active is False

    events = [
        json.loads(line)
        for line in (workspace / "logs" / "daemon_tick.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(e.get("event") == "scheduler_tick" for e in events)
    assert not any(e.get("event") == "budget_kill_switch" for e in events)
