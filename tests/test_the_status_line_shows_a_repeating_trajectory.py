"""Строка состояния показывает траекторию, а не только последний тик.

ПОЛЕВОЕ ОБНОВЛЕНИЕ 2026-08-25 (сводка оператора), «Beyond Suspicious Steps:
Ontological Trust in Long-Horizon Agents», 18 августа. Утверждение: проверять
каждое действие ЛОКАЛЬНО недостаточно — все шаги могут быть допустимыми, а вся
траектория уже стала другой задачей. Пошаговые метрики у них дают максимум
73,49 % AUC против >93 % на уровне траектории.

ЗАМЕР НА НАШИХ ЖИВЫХ ДАННЫХ, и он воспроизводит класс полностью.

    цель            «Найди в своём коде конкретный дефект, докажи его чтением»
    действие        improve_failure_to_idea_pipeline — 150 циклов, ВСЕ одно
    запусков        146 из 150 циклов были ЦИКЛОМ №1 свежего запуска
    длительность    27 часов, 15–16 августа
    цена            300 вызовов модели, 1773 единицы — 59 % всех денег кампаний
    продукт         142 «заявки» = 6 различных текстов, часть из них вида
                    `approvals_pending=1`, то есть не работа, а состояние

Ни одна пошаговая проверка не сработала, и это правильно: каждый шаг был
допустим. Траекторный страж У НАС ЕСТЬ — исход `repeat`, «уже пробовали в этой
кампании», — но его память живёт ОДИН ЗАПУСК, а траектория шла 146 запусков.
Поэтому он сработал 4 раза из 150.

ПОЧЕМУ ЗДЕСЬ НЕ ВОРОТА, А ЧТЕНИЕ. Расширить память стража на всю цель нельзя
без различителя: замер показал, что то же правило заблокировало бы законное —
`observe` 18 раз за сутки (это сенсор, повторяться его работа) и
`study_external_source` 10 раз. Единственный найденный различитель числовой
(доля новых продуктов 0,47 против 0,90–1,00), а выводить порог из восьми живых
пар значит настроить его на случаи, которые его породили — ровно провал формы
F-2. Правило из этих улик честно не выводится.

Зато данные о траектории УЖЕ лежат в ленте кампании, и 27 часов их никто не
читал. Здесь закрывается это: повторяющаяся траектория видна на той строке,
которую оператор и так смотрит. Решение, останавливать ли, остаётся за ним.
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
