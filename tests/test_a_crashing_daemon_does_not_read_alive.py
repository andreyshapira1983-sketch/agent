"""Демон, падающий каждый тик, не должен читаться живым.

ИСТОРИЧЕСКИЙ КЛАСС (H-38, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
CrowdStrike, 2024: обновились не программы, а ФАЙЛ ДАННЫХ, и он уронил каждого
потребителя разом. Здесь та же форма: испорченный `config/budget_limits.json`
роняет тик, и будет ронять его каждые полчаса всю неделю.

ЗАМЕР 2026-08-24, сквозной. Три подряд упавших тика, после чего `--status`
печатает: «Daemon: alive — last tick 0.0 min ago (event=tick_error)». Пульс
пишется и на ветке отказа, поэтому свежесть отметки говорит «жив», а правда
лежит в скобках после неё. Оператор, глянувший за неделю одну строку, прочтёт
первое слово.

ЧТО ЗДЕСЬ ЧИНИТСЯ, А ЧТО НЕТ. Само падение на испорченном конфиге — правильное
поведение: тратить по неизвестному потолку нельзя (H-33). Решение о вечном
повторе — это MIR-135, и оно открыто по отдельному решению. Здесь чинится
только СЛОВО: строка, у которой в руках есть `event=tick_error`, не имеет права
вести словом «alive».
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
