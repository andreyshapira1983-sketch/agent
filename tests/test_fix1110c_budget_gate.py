"""The hourly dollar limit acts before every model call; a campaign stops cleanly on a broken reading."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.best_next_action import BestNextAction
from core.budget_ledger import BudgetLedger, BudgetWindow
from core.campaign import CampaignConfig, CampaignLedger, _wait_for_usd_limit, run_campaign
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger


def _workspace(tmp_path: Path, *, spent_tokens: int = 0, limit: object = None,
               model: str = "deepseek-v4-pro") -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    if spent_tokens:
        row = {"model": model, "cache_hit_tokens": 0, "input_tokens": spent_tokens,
               "output_tokens": 0,
               "completed_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}
        (tmp_path / "data" / "model_usage.jsonl").write_text(json.dumps(row) + "\n",
                                                             encoding="utf-8")
    if limit is not None:
        (tmp_path / "config" / "budget_limits.json").write_text(
            json.dumps({"usd_per_hour": limit}), encoding="utf-8")
    return tmp_path


def _start(ws: Path, budget_ledger: BudgetLedger | None = None) -> None:
    ledger = ModelUsageLedger(path=ws / "data" / "model_usage.jsonl", budget_ledger=budget_ledger)
    ledger.assert_can_start(role="planner", provider="deepseek", model="deepseek-flash")


def test_a_call_is_refused_when_the_hours_dollars_reach_the_limit(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=1_000_000, limit=0.5)  # at least $0.66 spent

    with pytest.raises(ModelBudgetExceeded) as caught:
        _start(ws)

    message = str(caught.value)
    assert "hourly dollar limit reached" in message and "$0.5" in message
    assert "planner:deepseek/deepseek-flash" in message
    assert caught.value.counter == "usd_per_hour"


def test_a_refused_call_leaves_no_phantom_reservation(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=1_000_000, limit=0.5)
    windows = BudgetLedger(path=ws / "data" / "budget_ledger.jsonl",
                           windows=(BudgetWindow("hour", 3600, {"llm_calls": 60}),))

    with pytest.raises(ModelBudgetExceeded):
        _start(ws, windows)

    assert windows.load_records() == []


def test_calls_pass_under_the_limit_and_without_one(tmp_path: Path) -> None:
    _start(_workspace(tmp_path / "under", spent_tokens=1_000_000, limit=5))
    _start(_workspace(tmp_path / "none", spent_tokens=1_000_000))
    _start(_workspace(tmp_path / "fresh", limit=0.5))  # no journal yet: nothing spent


def test_a_limits_file_corrupted_mid_run_refuses_the_call(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=10)
    (ws / "config" / "budget_limits.json").write_text('{"usd_per_hour": 2,', encoding="utf-8")

    with pytest.raises(ModelBudgetExceeded, match=r"budget_limits\.json") as caught:
        _start(ws)
    assert "cannot be checked" in str(caught.value)


def test_an_unreadable_journal_refuses_the_call_when_a_limit_is_set(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, limit=0.5)
    (ws / "data" / "model_usage.jsonl").mkdir()

    with pytest.raises(ModelBudgetExceeded, match=r"model_usage\.jsonl"):
        ModelUsageLedger(path=ws / "data" / "model_usage.jsonl").assert_can_start(
            role="planner", provider="deepseek", model="deepseek-flash")


def test_the_refusal_shows_tokens_counted_by_the_estimate(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=1_000_000, limit=0.5, model="gpt-unknown")

    with pytest.raises(ModelBudgetExceeded, match="gpt-unknown 1000000 tokens"):
        _start(ws)


def test_a_negative_turn_limit_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TURN_MAX_USD", "-3")
    with pytest.raises(ValueError, match="AGENT_TURN_MAX_USD"):
        ModelUsageLedger.from_env()


# ---- campaign: its own wait stays, a broken reading is a recorded stop ----

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _wait(ws: Path, ledger: CampaignLedger, sleeps: list[float]) -> object:
    return _wait_for_usd_limit(ws, ledger, 1, "goal", _now, sleeps.append, 5.0)


def test_campaign_still_waits_at_the_limit(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=1_000_000, limit=0.5, model="gpt-unknown")
    ledger, sleeps = CampaignLedger(path=ws / "data" / "campaign_ledger.jsonl"), []

    assert _wait(ws, ledger, sleeps) is True
    assert sleeps == [60.0]
    assert ledger.records[-1].result == "waiting"
    assert "gpt-unknown 1000000 tokens" in ledger.records[-1].reason


def test_campaign_goes_on_under_the_limit(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=1_000_000, limit=5)
    ledger, sleeps = CampaignLedger(path=ws / "data" / "campaign_ledger.jsonl"), []

    assert _wait(ws, ledger, sleeps) is False
    assert sleeps == [] and ledger.records == []


def _observe() -> BestNextAction:
    return BestNextAction(action="observe", title="observe", severity="none", priority=0,
                          reason="nothing pressing", risk="read_only")


def _run(ws: Path) -> tuple[object, CampaignLedger]:
    ledger = CampaignLedger(path=ws / "data" / "campaign_ledger.jsonl")
    result = run_campaign(
        CampaignConfig(max_cycles=3, max_idle_streak=5), agent=SimpleNamespace(log=None),
        workspace=ws, gather_signals=lambda *_a, **_k: {"action": _observe()},
        execute_action=lambda **_k: pytest.fail("no work on a broken budget reading"),
        ledger=ledger, sleep_fn=lambda _s: None)
    return result, ledger


def test_campaign_stops_cleanly_on_a_corrupt_limits_file(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, spent_tokens=10)
    (ws / "config" / "budget_limits.json").write_text('{"usd_per_hour": 2,', encoding="utf-8")

    result, ledger = _run(ws)

    assert result.status == "stopped"
    assert result.stop_reason.startswith(
        "hourly dollar limit cannot be checked: budget config is not valid JSON")
    assert str(ws / "config" / "budget_limits.json") in result.stop_reason
    assert [r.result for r in ledger.records] == ["stopped"]
    assert ledger.records[0].reason == result.stop_reason


def test_campaign_stops_cleanly_on_an_unreadable_spend_journal(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, limit=0.5)
    (ws / "data" / "model_usage.jsonl").mkdir()

    result, _ledger = _run(ws)

    assert result.status == "stopped"
    assert "model_usage.jsonl" in result.stop_reason
