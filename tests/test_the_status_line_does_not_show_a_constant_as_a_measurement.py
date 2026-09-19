"""Постоянная величина не выдаётся оператору за измерение.

Замер, отвергнутые варианты и границы: F-5 в docs/audit/FIELD_CHECK_QUEUE.md, H-41 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import agent_tick


def _heartbeat(workspace, *, mode: str) -> None:
    (workspace / "data").mkdir(parents=True, exist_ok=True)
    (workspace / "data" / "daemon_heartbeat.json").write_text(
        json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "tick_complete",
            "mode": mode,
            "effects": "disabled" if mode == "dry_run" else "enabled",
            "processed_effects": 0,
            "dry_run_streak": 3,
        }),
        encoding="utf-8",
    )


def test_the_constant_is_not_printed_as_a_number(tmp_path, capsys) -> None:
    _heartbeat(tmp_path, mode="dry_run")

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    mode_line = next((ln for ln in printed.splitlines() if ln.startswith("Mode:")), "")
    assert mode_line, printed
    assert "processed_effects=0" not in mode_line, (
        "постоянная выдаётся за измерение: поле всегда 0 по построению, а в "
        "строке выглядит счётчиком применённых эффектов — " + mode_line
    )


def test_the_real_sensors_survive(tmp_path, capsys) -> None:
    """Контроль: чинится ОДНО поле, а не строка целиком."""
    _heartbeat(tmp_path, mode="live")

    agent_tick._print_status(tmp_path)
    mode_line = next(
        (ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("Mode:")),
        "",
    )

    assert "live" in mode_line
    assert "effects=enabled" in mode_line
    assert "dry_run_streak=3" in mode_line


def test_the_heartbeat_schema_is_untouched(tmp_path) -> None:
    """Граница: схему пульса не трогаем — её парсят кампания и память.

    Чинится ПОКАЗ, а не запись: поле остаётся в пульсе, потому что его
    отсутствие сломало бы читателей, которых мы не проверяли.
    """
    visibility = agent_tick._dry_run_visibility(
        dry_run=True, previous_streak=2, processed_effects=0, did_work=True,
    )

    assert "processed_effects" in visibility
    assert visibility["dry_run_streak"] == 3
