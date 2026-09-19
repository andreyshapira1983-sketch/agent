"""Отказ выбора цели виден в строке состояния, а не только в логах демона.

Замер, отвергнутые варианты и границы: MIR-154 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.charter_goal import charter_status_lines


def _decisions(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "data" / "charter_decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps({"payload": r}, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return tmp_path


def _row(hours_ago: float, status: str, reason: str = "") -> dict:
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"ts": when.isoformat(), "status": status, "goal": "g", "reason": reason}


def test_an_unreadable_veto_list_becomes_a_visible_line(tmp_path: Path) -> None:
    """Отказ-по-умолчанию обязан быть видимым, иначе это «живой, но без работы».

    Список отзывов, ставший нечитаемым, останавливает выбор цели навсегда.
    Незамеченный, он повторил бы MIR-135: пульс идёт, работы нет.
    """
    ws = _decisions(tmp_path, [
        _row(3, "declined", "operator veto list unreadable: config/vetoed_goals.txt"),
        _row(1, "declined", "operator veto list unreadable: config/vetoed_goals.txt"),
    ])

    lines = charter_status_lines(ws)

    assert lines, "выбор цели молчит вторые сутки, и в состоянии об этом ни слова"
    assert "veto list unreadable" in lines[0]
    assert "2x" in lines[0]


def test_a_working_chooser_says_nothing(tmp_path: Path) -> None:
    """Контроль: строка не должна появляться, когда цели выбираются."""
    ws = _decisions(tmp_path, [
        _row(5, "declined", "goal length 12 outside 20..300"),
        _row(2, "proposed"),
    ])

    assert charter_status_lines(ws) == []


def test_old_declines_are_not_today_s_problem(tmp_path: Path) -> None:
    """Граница окна: отказы недельной давности не кричат сегодня."""
    ws = _decisions(tmp_path, [_row(50, "declined", "old")])

    assert charter_status_lines(ws) == []


def test_no_file_is_not_an_error(tmp_path: Path) -> None:
    """Строка состояния не имеет права ронять тик."""
    assert charter_status_lines(tmp_path) == []
