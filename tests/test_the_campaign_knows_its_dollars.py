"""Кампания знает свой расход в долларах за час и держит предел (план субботы, пункт з).

Цены — официальная страница DeepSeek, 24.09.2026 (за 1 млн токенов, пик):
flash 0.006 / 0.30 / 1.20, v4-pro 0.044 / 1.32 / 3.96; вне пика — половина.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.campaign_ledger import CampaignCycleRecord, CampaignLedger
from core.usd_spend import is_peak, row_usd, usd_hour_limit, usd_last_hour

_PEAK = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)      # четверг, 07:00 UTC
_OFF = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _row(model: str, when: datetime, hit=0, miss=1_000_000, out=0) -> dict:
    return {"model": model, "completed_at": when.isoformat(), "cache_hit_tokens": hit,
            "cache_miss_tokens": miss, "input_tokens": hit + miss, "output_tokens": out}


def test_prices_follow_the_official_page() -> None:
    assert is_peak(_PEAK) and not is_peak(_OFF)
    assert row_usd(_row("deepseek-v4-pro", _PEAK)) == 1.32
    assert row_usd(_row("deepseek-v4-pro", _OFF)) == 0.66
    assert row_usd(_row("deepseek-flash", _PEAK, miss=0, out=1_000_000)) == 1.20
    assert row_usd(_row("deepseek-chat", _PEAK)) == 0.30, "old name is priced as Flash"
    assert row_usd(_row("gpt-unknown", _PEAK)) is None


def _workspace(tmp_path: Path, rows: list[dict], limit: float | None = None) -> Path:
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "model_usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if limit is not None:
        (tmp_path / "config" / "budget_limits.json").write_text(json.dumps({"usd_per_hour": limit}))
    return tmp_path


def test_only_the_last_hour_counts(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    ws = _workspace(tmp_path, [_row("deepseek-v4-pro", now - timedelta(minutes=10)),
                               _row("deepseek-v4-pro", now - timedelta(hours=3))])
    assert usd_last_hour(ws) == round(row_usd(_row("deepseek-v4-pro", now - timedelta(minutes=10))), 4)


def test_every_cycle_row_carries_the_hours_spend(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    ws = _workspace(tmp_path, [_row("deepseek-flash", now - timedelta(minutes=5))])
    ledger = CampaignLedger(path=ws / "data" / "campaign_ledger.jsonl")
    ledger.append(CampaignCycleRecord(
        cycle=1, ts=now.isoformat(), goal="g", action="a", action_title="t", severity="low",
        priority=0, risk="read_only", idle=False, llm_calls_spent=1, cost_units_spent=1,
        result="completed", reason="r"))
    row = json.loads((ws / "data" / "campaign_ledger.jsonl").read_text(encoding="utf-8"))
    assert row["usd_last_hour"] > 0


def test_the_limit_is_optional(tmp_path: Path) -> None:
    assert usd_hour_limit(_workspace(tmp_path, [])) is None
    assert usd_hour_limit(_workspace(tmp_path / "b", [], limit=0.5)) == 0.5
