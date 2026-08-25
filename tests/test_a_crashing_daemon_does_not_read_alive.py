"""Демон, падающий каждый тик, не должен читаться живым.

Замер, отвергнутые варианты и границы: H-38 в docs/audit/HISTORICAL_FAILURE_LEDGER.md, H-33 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import agent_tick


def _heartbeat(workspace: pathlib.Path, *, event: str) -> None:
    from datetime import datetime, timezone

    (workspace / "data").mkdir(parents=True, exist_ok=True)
    (workspace / "data" / "daemon_heartbeat.json").write_text(
        json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "mode": "dry_run",
        }),
        encoding="utf-8",
    )


@pytest.mark.parametrize("event", ["tick_error", "tick_exception"])
def test_a_failing_last_tick_is_not_called_alive(tmp_path, capsys, event: str) -> None:
    _heartbeat(tmp_path, event=event)

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    daemon_line = next(
        (ln for ln in printed.splitlines() if ln.startswith("Daemon:")), ""
    )
    assert daemon_line, "строки о демоне нет вовсе:\n" + printed
    assert "alive" not in daemon_line, (
        "свежая отметка падения читается как «жив» — правда осталась в скобках: "
        + daemon_line
    )
    assert event in daemon_line, (
        "причина потерялась — тогда строка честна, но бесполезна: " + daemon_line
    )


def test_a_healthy_tick_still_reads_alive(tmp_path, capsys) -> None:
    """Контроль: иначе тест проходил бы и на строке, где «alive» нет никогда."""
    _heartbeat(tmp_path, event="tick_complete")

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    daemon_line = next(
        (ln for ln in printed.splitlines() if ln.startswith("Daemon:")), ""
    )
    assert "alive" in daemon_line, daemon_line
