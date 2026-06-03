from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.budget_ledger import BudgetLedger, BudgetWindow


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch):
    # Operator shells may export AGENT_BUDGET_* vars; strip them so tests
    # that rely on `BudgetLedger.from_env` see a clean environment.
    for name in list(os.environ):
        if name.startswith("AGENT_BUDGET_"):
            monkeypatch.delenv(name, raising=False)


def test_budget_ledger_reserves_and_blocks_inside_window(tmp_path: Path):
    ledger = BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=(BudgetWindow("day", 86400, {"llm_calls": 1}),),
    )

    first = ledger.reserve("llm_calls", reason="planner", now=NOW)
    second = ledger.reserve("llm_calls", reason="synthesizer", now=NOW)

    assert first.allowed is True
    assert second.allowed is False
    assert second.window == "day"
    assert second.used == 1
    assert second.limit == 1
    assert len((tmp_path / "budget.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_budget_ledger_ignores_records_outside_window(tmp_path: Path):
    path = tmp_path / "budget.jsonl"
    old = NOW - timedelta(days=2)
    ledger = BudgetLedger(
        path=path,
        windows=(BudgetWindow("day", 86400, {"llm_calls": 1}),),
    )
    ledger.reserve("llm_calls", reason="old", now=old)

    fresh = BudgetLedger(
        path=path,
        windows=(BudgetWindow("day", 86400, {"llm_calls": 1}),),
    )
    decision = fresh.reserve("llm_calls", reason="fresh", now=NOW)

    assert decision.allowed is True
    assert fresh.snapshot(now=NOW)["windows"][0]["counters"]["llm_calls"]["used"] == 1


def test_budget_ledger_records_actual_usage_without_enforcement(tmp_path: Path):
    ledger = BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=(BudgetWindow("hour", 3600, {"model_tokens": 10}),),
    )

    ledger.record("model_tokens", amount=25, reason="actual usage", now=NOW)
    snapshot = ledger.snapshot(now=NOW)

    assert snapshot["windows"][0]["counters"]["model_tokens"] == {
        "used": 25,
        "limit": 10,
    }


def test_budget_ledger_skips_malformed_jsonl_records(tmp_path: Path):
    path = tmp_path / "budget.jsonl"
    path.write_text(
        '{"counter":"llm_calls","amount":1,"created_at":"2026-05-30T12:00:00+00:00"}\n'
        'not-json\n'
        '{"counter":"llm_calls","amount":"bad"}\n',
        encoding="utf-8",
    )
    ledger = BudgetLedger(
        path=path,
        windows=(BudgetWindow("day", 86400, {"llm_calls": 2}),),
    )

    assert ledger.snapshot(now=NOW)["windows"][0]["counters"]["llm_calls"]["used"] == 1


def test_budget_ledger_from_env_builds_hour_and_day_windows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_HOUR_LLM_CALLS", "2")
    monkeypatch.setenv("AGENT_BUDGET_DAY_MODEL_COST_UNITS", "30")

    ledger = BudgetLedger.from_env(path=tmp_path / "budget.jsonl")
    snapshot = ledger.snapshot(now=NOW)

    assert [window["name"] for window in snapshot["windows"]] == ["hour", "day"]
    assert snapshot["windows"][0]["counters"]["llm_calls"]["limit"] == 2
    assert snapshot["windows"][1]["counters"]["model_cost_units"]["limit"] == 30


def test_budget_ledger_from_config_file_with_env_override(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "budget_limits.json"
    config_path.write_text(
        """
{
  "windows": {
    "hour": {"llm_calls": 5, "model_tokens": 1000},
    "day": {"llm_calls": 20, "model_cost_units": 50}
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BUDGET_HOUR_LLM_CALLS", "7")

    ledger = BudgetLedger.from_env(
        path=tmp_path / "budget.jsonl",
        config_path=config_path,
    )
    snapshot = ledger.snapshot(now=NOW)

    assert snapshot["config_path"] == str(config_path.resolve())
    assert snapshot["windows"][0]["counters"]["llm_calls"]["limit"] == 7
    assert snapshot["windows"][0]["counters"]["model_tokens"]["limit"] == 1000
    assert snapshot["windows"][1]["counters"]["llm_calls"]["limit"] == 20
    assert snapshot["windows"][1]["counters"]["model_cost_units"]["limit"] == 50


def test_budget_window_rejects_invalid_values():
    with pytest.raises(ValueError):
        BudgetWindow("bad", 0, {})

    with pytest.raises(ValueError):
        BudgetWindow("bad", 1, {"llm_calls": -1})
