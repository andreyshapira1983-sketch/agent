"""Dollar spend readings fail closed: unreadable journal, long busy hour, garbage tokens, odd turn limit."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import usd_spend
from core.usd_spend import (
    row_usd,
    turn_usd_limit,
    unpriced_note,
    usd_hour_refusal,
    usd_last_hour,
    usd_since,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)  # Tuesday, off-peak


def _row(when: datetime, model: str = "deepseek-v4-pro", **extra: object) -> dict:
    return {"model": model, "completed_at": when.isoformat(), "cache_hit_tokens": 0,
            "input_tokens": 1_000_000, "output_tokens": 0, **extra}


def _workspace(tmp_path: Path, rows: list[dict], limit: float | None = None) -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "model_usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if limit is not None:
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "budget_limits.json").write_text(
            json.dumps({"usd_per_hour": limit}), encoding="utf-8")
    return tmp_path


def test_no_journal_yet_is_zero_spend(tmp_path: Path) -> None:
    assert usd_last_hour(tmp_path, NOW) == 0.0


def test_unreadable_journal_is_not_zero_spend(tmp_path: Path) -> None:
    (tmp_path / "data" / "model_usage.jsonl").mkdir(parents=True)  # exists, cannot be read

    with pytest.raises(ValueError, match=r"model_usage\.jsonl") as caught:
        usd_last_hour(tmp_path, NOW)
    assert "not readable" in str(caught.value)


def test_a_busy_hour_longer_than_the_tail_is_counted_whole(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usd_spend, "_TAIL_BYTES", 400)
    rows = [_row(NOW - timedelta(hours=2))] + [
        _row(NOW - timedelta(minutes=50 - i)) for i in range(40)]
    ws = _workspace(tmp_path, rows)

    one = row_usd(rows[-1])
    assert usd_last_hour(ws, NOW) == round(40 * one, 4)


def test_a_journal_younger_than_the_hour_is_read_whole(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usd_spend, "_TAIL_BYTES", 400)
    rows = [_row(NOW - timedelta(minutes=30)) for _ in range(40)]

    assert usd_last_hour(_workspace(tmp_path, rows), NOW) == round(40 * row_usd(rows[0]), 4)


@pytest.mark.parametrize("field, value", [
    ("input_tokens", "many"), ("output_tokens", [1]), ("cache_hit_tokens", "1e3"),
    ("output_tokens", -5), ("input_tokens", True),
])
def test_a_garbage_token_field_names_the_file_and_the_field(
        tmp_path: Path, field: str, value: object) -> None:
    ws = _workspace(tmp_path, [_row(NOW - timedelta(minutes=5), **{field: value})])

    with pytest.raises(ValueError) as caught:
        usd_since(ws, NOW - timedelta(hours=1))
    assert "model_usage.jsonl" in str(caught.value) and field in str(caught.value)


def test_a_garbage_row_outside_the_window_does_not_stop_the_reading(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_row(NOW - timedelta(hours=5), input_tokens="many"),
                               _row(NOW - timedelta(minutes=5))])
    assert usd_since(ws, NOW - timedelta(hours=1)) > 0


def test_negative_turn_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TURN_MAX_USD", "-1")
    with pytest.raises(ValueError, match="AGENT_TURN_MAX_USD"):
        turn_usd_limit()


@pytest.mark.parametrize("raw", ["a lot", "nan", "inf"])
def test_garbage_turn_limit_falls_back_and_is_reported_once(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str) -> None:
    monkeypatch.setenv("AGENT_TURN_MAX_USD", raw)
    monkeypatch.setattr(usd_spend, "_WARNED_TURN_LIMIT", set())

    with caplog.at_level(logging.WARNING, logger="core.usd_spend"):
        assert turn_usd_limit() == usd_spend.TURN_MAX_USD
        assert turn_usd_limit() == usd_spend.TURN_MAX_USD

    reports = [r for r in caplog.records if "AGENT_TURN_MAX_USD" in r.getMessage()]
    assert len(reports) == 1 and raw in reports[0].getMessage()


def test_turn_limit_zero_unset_and_plain_values_keep_their_meaning(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TURN_MAX_USD", raising=False)
    assert turn_usd_limit() == usd_spend.TURN_MAX_USD
    monkeypatch.setenv("AGENT_TURN_MAX_USD", "0")
    assert turn_usd_limit() is None
    monkeypatch.setenv("AGENT_TURN_MAX_USD", "2.5")
    assert turn_usd_limit() == 2.5


def test_hour_refusal_is_none_without_a_limit_or_under_it(tmp_path: Path) -> None:
    rows = [_row(NOW - timedelta(minutes=5))]  # $0.66 off-peak
    assert usd_hour_refusal(_workspace(tmp_path / "a", rows), NOW) is None
    assert usd_hour_refusal(_workspace(tmp_path / "b", rows, limit=5), NOW) is None


def test_hour_refusal_states_spend_and_limit(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_row(NOW - timedelta(minutes=5))], limit=0.002)

    refusal = usd_hour_refusal(ws, NOW)

    assert refusal is not None and "$0.66" in refusal and "$0.002" in refusal
    assert "hour" in refusal and "estimate" not in refusal


def test_hour_refusal_shows_tokens_counted_by_the_estimate(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_row(NOW - timedelta(minutes=5), model="gpt-unknown")], limit=0.5)

    refusal = usd_hour_refusal(ws, NOW)

    assert refusal is not None and "gpt-unknown" in refusal and "1000000" in refusal
    assert "estimate" in refusal
    assert unpriced_note(ws, NOW - timedelta(hours=1)) in refusal
