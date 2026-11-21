"""An unreadable or odd market ledger is reported, never shown as zero orders."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.market_ledger import LedgerUnreadable, MarketLedger
from core.work_usefulness import market_accepted
from scripts import market_worker as market_script


def _order(i: int) -> dict:
    return {"assignment": {"assignmentId": f"a{i}", "jobId": f"j{i}", "escrowAmount": "5", "status": "accepted"},
            "job": {"title": f"order {i}"}}


def _workspace_ledger(tmp_path: Path) -> MarketLedger:
    ledger = MarketLedger(tmp_path / "data" / "market" / "ledger.json")
    for i in (1, 2):
        ledger.add_run(_order(i), 60, 0.1)
    return ledger


def _tear(ledger: MarketLedger) -> None:
    raw = ledger.path.read_text(encoding="utf-8")
    ledger.path.write_text(raw[: len(raw) // 2], encoding="utf-8")


def test_ledger_command_reports_an_unreadable_ledger(tmp_path: Path, capsys) -> None:
    ledger = _workspace_ledger(tmp_path)
    _tear(ledger)

    code = market_script.main(["--ledger", "--workspace", str(tmp_path)])

    out = capsys.readouterr()
    assert code != 0
    assert "ledger.json" in out.err
    assert '"orders"' not in out.out


def test_ledger_command_still_shows_a_healthy_ledger(tmp_path: Path, capsys) -> None:
    _workspace_ledger(tmp_path)

    code = market_script.main(["--ledger", "--workspace", str(tmp_path)])

    assert code == 0
    assert '"orders": 2' in capsys.readouterr().out
    assert market_accepted(tmp_path) == 2


def test_unknown_field_in_a_row_is_tolerated_and_survives_a_rewrite(tmp_path: Path) -> None:
    ledger = _workspace_ledger(tmp_path)
    raw = json.loads(ledger.path.read_text(encoding="utf-8"))
    raw["a1"]["note"] = "ручная пометка"
    ledger.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert sorted(ledger.lines()) == ["a1", "a2"]
    ledger.mark("a2", claude_minutes=3)

    saved = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert saved["a1"]["note"] == "ручная пометка"
    assert saved["a2"]["claude_minutes"] == 3
    assert "extra" not in saved["a1"]


def test_row_that_is_not_an_object_names_the_file_and_the_row(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"a1": ["not", "a", "row"]}), encoding="utf-8")

    with pytest.raises(LedgerUnreadable, match=r"ledger\.json.*a1"):
        MarketLedger(path).lines()
