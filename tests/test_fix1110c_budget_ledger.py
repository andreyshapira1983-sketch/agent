"""Persistent budget windows: the allowed line names its limit; odd limits and unreadable files fail closed."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.budget_ledger import BudgetLedger, BudgetWindow, _limits_from_config, _load_budget_config

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def test_allowed_reservation_reports_the_enforced_window(tmp_path: Path) -> None:
    log = _Log()
    ledger = BudgetLedger(path=tmp_path / "b.jsonl", logger=log,
                          windows=(BudgetWindow("hour", 3600, {"llm_calls": 60}),))

    ledger.reserve("llm_calls", now=NOW)
    decision = ledger.reserve("llm_calls", now=NOW)

    assert decision.allowed is True
    assert (decision.window, decision.used, decision.limit) == ("hour", 2, 60)
    payload = [p for event, p in log.events if event == "budget_window_reserved"][-1]
    assert payload["limit_label"] == "60" and payload["limit_enforced"] is True


def test_allowed_reservation_reports_the_tightest_window(tmp_path: Path) -> None:
    ledger = BudgetLedger(path=tmp_path / "b.jsonl", windows=(
        BudgetWindow("hour", 3600, {"llm_calls": 60}),
        BudgetWindow("day", 86400, {"llm_calls": 5}),
    ))

    decision = ledger.reserve("llm_calls", amount=2, now=NOW)

    assert (decision.window, decision.used, decision.limit) == ("day", 2, 5)


def test_unlimited_only_when_no_window_limits_the_counter(tmp_path: Path) -> None:
    ledger = BudgetLedger(path=tmp_path / "b.jsonl", windows=(
        BudgetWindow("hour", 3600, {"llm_calls": 60}),))

    payload = ledger.reserve("web_fetches", now=NOW).to_dict()

    assert payload["window"] is None and payload["limit_label"] == "unlimited"
    assert payload["limit_enforced"] is False


@pytest.mark.parametrize("value", [True, False, 2.9, "2.9", None, [3], float("inf")])
def test_a_limit_that_is_not_a_whole_number_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match=r"hour\.llm_calls"):
        _limits_from_config({"hour": {"llm_calls": value}}, "hour")


def test_whole_number_limits_are_still_accepted() -> None:
    limits = _limits_from_config({"hour": {"llm_calls": 60, "web_fetches": "7", "cycles": 3.0}},
                                 "hour")
    assert limits == {"llm_calls": 60, "web_fetches": 7, "cycles": 3}


def test_a_non_utf8_config_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "budget_limits.json"
    path.write_bytes(b'{"hour": {"llm_calls": 60}, "note": "\xff\xfe"}')

    with pytest.raises(ValueError, match="budget config is not readable") as caught:
        _load_budget_config(path)
    assert str(path) in str(caught.value)


def test_an_unreadable_config_is_a_value_error(tmp_path: Path) -> None:
    path = tmp_path / "budget_limits.json"
    path.mkdir()  # a directory: exists, cannot be read

    with pytest.raises(ValueError, match="budget config is not readable"):
        _load_budget_config(path)


def test_a_valid_config_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "budget_limits.json"
    path.write_text(json.dumps({"hour": {"llm_calls": 60}}), encoding="utf-8")
    assert _load_budget_config(path) == {"hour": {"llm_calls": 60}}


def test_kill_switch_docstring_names_the_real_command() -> None:
    from core.budget_kill_switch import BudgetKillSwitch

    assert ":budget-kill-switch --clear" in (BudgetKillSwitch.load.__doc__ or "")
