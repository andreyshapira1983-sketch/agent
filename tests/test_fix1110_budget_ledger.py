"""A negative budget limit is refused, not read as unlimited."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.budget_ledger import BudgetLedger


def test_negative_config_limit_is_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "budget_limits.json"
    cfg.write_text(json.dumps({"day": {"llm_calls": -5}}), encoding="utf-8")

    with pytest.raises(ValueError, match=r"day\.llm_calls must be a non-negative integer"):
        BudgetLedger.from_env(path=tmp_path / "ledger.jsonl", config_path=cfg)


def test_negative_env_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUDGET_CONFIG_PATH", raising=False)
    monkeypatch.setenv("AGENT_BUDGET_DAY_LLM_CALLS", "-5")

    with pytest.raises(ValueError, match="AGENT_BUDGET_DAY_LLM_CALLS must be a non-negative integer"):
        BudgetLedger.from_env(path=tmp_path / "ledger.jsonl")


def test_zero_and_positive_limits_still_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BUDGET_CONFIG_PATH", raising=False)
    cfg = tmp_path / "budget_limits.json"
    cfg.write_text(json.dumps({"day": {"llm_calls": 0, "cycles": 7}}), encoding="utf-8")
    monkeypatch.setenv("AGENT_BUDGET_HOUR_LLM_CALLS", "3")

    ledger = BudgetLedger.from_env(path=tmp_path / "ledger.jsonl", config_path=cfg)

    hour, day = ledger.windows
    assert hour.limit_for("llm_calls") == 3
    assert day.limit_for("llm_calls") == 0
    assert day.limit_for("cycles") == 7
