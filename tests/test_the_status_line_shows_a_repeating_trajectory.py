"""Строка состояния показывает траекторию, а не только последний тик.

Замер, отвергнутые варианты и границы: F-2 в docs/audit/FIELD_CHECK_QUEUE.md.
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import agent_tick


def _ledger(workspace: pathlib.Path, rows: list[dict]) -> None:
    from core.state_integrity import append_state_jsonl

    (workspace / "data").mkdir(parents=True, exist_ok=True)
    append_state_jsonl(workspace / "data" / "campaign_ledger.jsonl", rows)


def _cycle(goal: str, action: str, when: datetime, **extra) -> dict:
    row = {
        "cycle": 1, "ts": when.isoformat(), "goal": goal, "action": action,
        "action_title": action, "severity": "info", "priority": 50,
        "risk": "reversible", "idle": False, "llm_calls_spent": 2,
        "cost_units_spent": 12, "result": "completed",
    }
    row.update(extra)
    return row


def test_a_repeating_trajectory_is_named(tmp_path, capsys) -> None:
    now = datetime.now(timezone.utc)
    _ledger(tmp_path, [
        _cycle("найди дефект и докажи", "improve_pipeline", now - timedelta(hours=h))
        for h in range(12)
    ])

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    assert "improve_pipeline" in printed, (
        "траектория из двенадцати одинаковых действий по одной цели не видна "
        "оператору — ровно то, что шло 27 часов и осталось незамеченным:\n"
        + printed
    )
    assert "12" in printed, "не назван РАЗМАХ повторения: " + printed


def test_the_cost_of_the_repetition_is_named(tmp_path, capsys) -> None:
    """Повтор без цены — любопытный факт; с ценой — основание для решения."""
    now = datetime.now(timezone.utc)
    _ledger(tmp_path, [
        _cycle("цель", "act", now - timedelta(hours=h), cost_units_spent=100)
        for h in range(6)
    ])

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    assert "600" in printed, "не названа цена повторяющейся траектории: " + printed


def test_varied_work_is_not_reported_as_repetition(tmp_path, capsys) -> None:
    """Контроль: разная работа по одной цели — не траектория-петля.

    Без него правило удовлетворялось бы надписью, печатаемой всегда, и первая
    же настоящая петля утонула бы в шуме.
    """
    now = datetime.now(timezone.utc)
    _ledger(tmp_path, [
        _cycle("цель", f"действие_{i}", now - timedelta(hours=i))
        for i in range(8)
    ])

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    assert "Trajectory" not in printed, (
        "восемь РАЗНЫХ действий объявлены повторяющейся траекторией: " + printed
    )


def test_an_old_trajectory_is_not_reported(tmp_path, capsys) -> None:
    """Граница: вчерашняя петля уже не идёт, и звать по ней незачем."""
    old = datetime.now(timezone.utc) - timedelta(days=9)
    _ledger(tmp_path, [
        _cycle("цель", "act", old - timedelta(hours=h)) for h in range(12)
    ])

    agent_tick._print_status(tmp_path)

    assert "Trajectory" not in capsys.readouterr().err


def test_a_missing_ledger_is_silent(tmp_path, capsys) -> None:
    """Строка состояния обязана печататься и там, где кампаний не было."""
    (tmp_path / "data").mkdir()

    agent_tick._print_status(tmp_path)

    assert "Trajectory" not in capsys.readouterr().err
