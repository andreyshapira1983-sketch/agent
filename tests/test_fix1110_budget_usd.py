"""The hourly dollar limit fails closed: corrupt limits file, unpriced models."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.usd_spend import (
    FALLBACK_PRICES,
    PEAK_PRICES,
    row_usd,
    unpriced_tokens_since,
    usd_hour_limit,
    usd_last_hour,
)


def _limits(tmp_path: Path, text: str) -> Path:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "budget_limits.json").write_text(text, encoding="utf-8")
    return tmp_path


def test_absent_limits_file_still_means_no_limit(tmp_path: Path) -> None:
    assert usd_hour_limit(tmp_path) is None


def test_absent_or_zero_key_still_means_no_limit(tmp_path: Path) -> None:
    assert usd_hour_limit(_limits(tmp_path / "a", '{"day": {"llm_calls": 5}}')) is None
    assert usd_hour_limit(_limits(tmp_path / "b", '{"usd_per_hour": 0}')) is None
    assert usd_hour_limit(_limits(tmp_path / "c", '{"usd_per_hour": null}')) is None


@pytest.mark.parametrize("text", [
    '{"usd_per_hour": 2, "day": {"llm_calls": 100}',  # torn write
    "[1, 2]",
    '{"usd_per_hour": "two"}',
    '{"usd_per_hour": -1}',
    '{"usd_per_hour": NaN}',
    '{"usd_per_hour": true}',
])
def test_corrupt_limits_file_is_not_unlimited(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValueError):
        usd_hour_limit(_limits(tmp_path, text))


def test_unreadable_limits_file_is_not_unlimited(tmp_path: Path) -> None:
    (tmp_path / "config" / "budget_limits.json").mkdir(parents=True)  # a directory: read fails
    with pytest.raises(ValueError):
        usd_hour_limit(tmp_path)


def _usage(tmp_path: Path, models: list[str], when: datetime) -> Path:
    (tmp_path / "data").mkdir(parents=True)
    rows = [{"model": m, "completed_at": when.isoformat(), "cache_hit_tokens": 0,
             "input_tokens": 1_000_000, "output_tokens": 1_000_000} for m in models]
    (tmp_path / "data" / "model_usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def test_fallback_is_the_dearest_known_rate() -> None:
    for i in range(3):
        assert FALLBACK_PRICES[i] == max(p[i] for p in PEAK_PRICES.values())


def test_unknown_model_is_not_free(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    ws = _usage(tmp_path, ["gpt-unknown"], now - timedelta(minutes=5))

    spent = usd_last_hour(ws, now)

    # Peak rate regardless of the hour: the estimate must not undercount.
    assert spent == round(FALLBACK_PRICES[1] + FALLBACK_PRICES[2], 4)
    assert row_usd({"model": "gpt-unknown", "input_tokens": 5}) is None, "no invented exact price"


def test_unpriced_tokens_are_reported_per_model(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    ws = _usage(tmp_path, ["deepseek-flash", "gpt-unknown", "gpt-unknown", ""], now - timedelta(minutes=5))

    assert unpriced_tokens_since(ws, now - timedelta(hours=1)) == {
        "gpt-unknown": 4_000_000, "<no model>": 2_000_000}
    assert unpriced_tokens_since(ws, now) == {}


def test_unknown_model_is_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    now = datetime.now(timezone.utc)
    ws = _usage(tmp_path, ["model-seen-once-1110", "model-seen-once-1110"], now - timedelta(minutes=5))

    with caplog.at_level("WARNING", logger="core.usd_spend"):
        usd_last_hour(ws, now)
        usd_last_hour(ws, now)

    assert len([r for r in caplog.records if "model-seen-once-1110" in r.getMessage()]) == 1
