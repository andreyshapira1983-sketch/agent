"""Потерянная строка состояния обязана быть видна оператору.

ИСТОРИЧЕСКИЙ КЛАСС (H-29, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Урок ZFS и сквозных контрольных сумм состоит из двух половин. Первая — заметить
порчу — у нас соблюдена: строка с неверным хешем уезжает в `.quarantine`, файл
переписывается без неё, улика цела. Вторая половина — не отдать молча МЕНЬШЕ
данных — не соблюдена: читатель получает список короче и ничем не отличает его
от полного.

ПОЧЕМУ ЭТО НЕ РАВНОЗНАЧНО ДЛЯ РАЗНЫХ ХРАНИЛИЩ. У обычного журнала потеря строки
теряет память. У СЧЁТЧИКА-ПОТОЛКА потеря строки выдаёт разрешение: замер
2026-08-24 — исчерпанный суточный потолок в три вызова после порчи одной строки
снова разрешает тратить, и ни предупреждения, ни записи об этом нет.

ЖИВЫХ СЛУЧАЕВ НЕТ: карантин по всем хранилищам пуст за четыре недели работы.
Поэтому здесь не меняется РЕШЕНИЕ (разрешать или нет) — оно про деньги и
принадлежит оператору. Здесь закрывается молчание.
"""
from __future__ import annotations

import json
import pathlib

from core.state_integrity import append_state_jsonl, read_state_jsonl


def _corrupt_one_row(path: pathlib.Path) -> None:
    """Испортить полезную нагрузку первой строки так, чтобы хеш перестал сходиться."""
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["payload"]["amount"] = 999
    lines[0] = json.dumps(row, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_corrupt_budget_row_silently_restores_spending_room(tmp_path) -> None:
    """Свидетель самой опасности — он остаётся зелёным и после починки.

    Это не тест на дефект, а зафиксированный ФАКТ: у потолка потеря строки
    работает в сторону разрешения. Если поведение когда-нибудь станет
    fail-closed, тест обязан покраснеть и потребовать пересмотра записи.
    """
    from core.budget_ledger import BudgetLedger

    cfg = tmp_path / "limits.json"
    cfg.write_text(json.dumps({"windows": {"day": {"llm_calls": 3}}}), encoding="utf-8")
    path = tmp_path / "budget.jsonl"

    ledger = BudgetLedger.from_env(path=path, config_path=cfg)
    for i in range(3):
        ledger.reserve("llm_calls", amount=1, reason=f"вызов {i}")
    assert not BudgetLedger.from_env(
        path=path, config_path=cfg
    ).reserve("llm_calls", amount=1, reason="четвёртый").allowed

    before = len(read_state_jsonl(path))
    _corrupt_one_row(path)
    assert len(read_state_jsonl(path)) == before - 1, "проба не легла: строка не выпала"

    after = BudgetLedger.from_env(path=path, config_path=cfg)
    assert after.reserve("llm_calls", amount=1, reason="после порчи").allowed, (
        "поведение изменилось — потолок больше не открывается порчей; "
        "перечитать H-29 и переписать запись, а не подгонять тест"
    )


def test_the_status_line_names_a_quarantined_row(tmp_path, capsys) -> None:
    """Молчание закрыто: оператор видит потерю там, где он и так смотрит."""
    import agent_tick

    (tmp_path / "data").mkdir()
    store = tmp_path / "data" / "budget_ledger.jsonl"
    append_state_jsonl(store, [{"counter": "llm_calls", "amount": 1}])
    append_state_jsonl(store, [{"counter": "llm_calls", "amount": 1}])
    _corrupt_one_row(store)
    read_state_jsonl(store)  # чтение и есть момент карантина

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    assert "budget_ledger" in printed and "quarantin" in printed.lower(), (
        "строка состояния молчит о потерянной строке — потеря видна только "
        "тому, кто заглянет в .quarantine, а туда никто не заглядывает:\n"
        + printed
    )


def test_a_clean_workspace_says_nothing_about_quarantine(tmp_path, capsys) -> None:
    """Контроль: строка обязана молчать, когда терять нечего.

    Без него первый тест проходил бы и на надписи, напечатанной всегда.
    """
    import agent_tick

    (tmp_path / "data").mkdir()
    store = tmp_path / "data" / "budget_ledger.jsonl"
    append_state_jsonl(store, [{"counter": "llm_calls", "amount": 1}])
    read_state_jsonl(store)

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err
    assert "quarantin" not in printed.lower(), printed
