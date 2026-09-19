"""Демон, падающий каждый тик, больше не может вечно зваться `alive`.

Замер, механизм и границы: MIR-135 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Форма — Horizon в миниатюре: сердцебиение пишется ДО работы тика, поэтому
падающий каждые 4 часа демон держит его вечно свежим, а `logs/daemon_tick.jsonl`
— единственное место, где исход тика записан, — не читал никто (один писатель,
ноль читателей, перепроверено 2026-08-27 на 743 строках). Запись системы о
себе, которую никто не допрашивает, — механизм всех тихих многолетних провалов.

Порог: 3 подряд `tick_error` (12 часов на 4-часовом расписании). Последствие:
вердикт `crash_loop_suspected` и блокировка «следующего действия» — совет
«всё хорошо» при падающем демоне был бы той самой ложью.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.heartbeat_io import (
    CRASH_LOOP_THRESHOLD,
    error_tick_streak,
    tick_log_path,
)


def _write_journal(workspace: Path, events: list[str]) -> None:
    path = tick_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps({"event": event}) + "\n")


def test_the_streak_counts_consecutive_error_ticks(tmp_path: Path) -> None:
    """Красный свидетель: серию не считал никто — теперь она считается."""
    _write_journal(tmp_path, [
        "tick_complete", "scheduler_tick", "tick_error",
        "scheduler_tick", "tick_error", "tick_error",
    ])

    assert error_tick_streak(tmp_path) == 3


def test_a_completed_tick_resets_the_streak(tmp_path: Path) -> None:
    _write_journal(tmp_path, ["tick_error", "tick_error", "tick_complete"])

    assert error_tick_streak(tmp_path) == 0


def test_a_kill_switch_tick_is_throttling_not_failure(tmp_path: Path) -> None:
    """Тик, остановленный рубильником бюджета, вёл себя как задумано."""
    _write_journal(tmp_path, ["tick_error", "tick_error", "budget_kill_switch"])

    assert error_tick_streak(tmp_path) == 0


def test_no_journal_and_broken_lines_mean_zero_not_a_crash(tmp_path: Path) -> None:
    """Незнание — не приговор: нет журнала — нет серии, битая строка — пропуск."""
    assert error_tick_streak(tmp_path) == 0

    path = tick_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{битая строка\n{"event": "tick_error"}\n', encoding="utf-8")
    assert error_tick_streak(tmp_path) == 1


def test_three_failing_ticks_change_the_verdict_and_block_next_action() -> None:
    """Свежее сердцебиение + серия падений ≠ «недавний тик, всё хорошо»."""
    from cli.commands_health import (
        _daemon_blocks_next_action,
        _enrich_daemon_payload,
    )

    scheduler = {"status": "known", "due": 0}
    failing = _enrich_daemon_payload(
        {"status": "alive", "error_streak": CRASH_LOOP_THRESHOLD}, scheduler
    )
    healthy = _enrich_daemon_payload(
        {"status": "alive", "error_streak": CRASH_LOOP_THRESHOLD - 1}, scheduler
    )

    assert failing["interpretation"] == "crash_loop_suspected"
    assert _daemon_blocks_next_action(failing) is True
    assert healthy["interpretation"] == "scheduled_daemon_recent_tick"
    assert _daemon_blocks_next_action(healthy) is False


def test_the_live_path_reads_the_streak_end_to_end(tmp_path: Path) -> None:
    """Сквозной свидетель дефекта: раньше этот полигон дал бы чистое `alive`."""
    import agent_tick
    from cli.commands_health import _daemon_payload, _enrich_daemon_payload

    agent_tick._write_heartbeat(tmp_path, {"event": "tick_error"})
    _write_journal(tmp_path, ["tick_error"] * CRASH_LOOP_THRESHOLD)

    now = datetime.now(timezone.utc)
    payload = _daemon_payload(tmp_path, now)
    enriched = _enrich_daemon_payload(payload, {"status": "known", "due": 0})

    assert payload["status"] == "alive", "свежесть честна: демон ходит"
    assert payload["error_streak"] == CRASH_LOOP_THRESHOLD
    assert enriched["interpretation"] == "crash_loop_suspected"


def test_the_tick_journal_finally_has_a_reader() -> None:
    """Ноль читателей — механизм Horizon; читатель обязан жить на пути здоровья.

    Пин по смыслу: имя связано в модуле здоровья и вызвано в сборке вердикта.
    """
    import inspect

    from cli import commands_health as mod

    assert callable(getattr(mod, "error_tick_streak", None))
    assert "error_tick_streak" in inspect.getsource(mod._daemon_payload)
