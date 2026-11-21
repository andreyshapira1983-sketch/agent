"""Зонд карты полномочий обязан видеть положительный случай, а не печатать ноль.

Замер, отвергнутые варианты и границы: MIR-150 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import pathlib

import pytest

from scripts.memory_authority_map import _accessors_for, _iter_py, path_aliases


@pytest.fixture(scope="module")
def files() -> list[pathlib.Path]:
    return _iter_py()


def test_a_constant_counts_as_the_address_of_the_store(files) -> None:
    """Адрес почти везде живёт в константе модуля, а не в теле функции."""
    aliases = path_aliases("budget_ledger.jsonl", files)

    assert aliases - {"budget_ledger.jsonl"}, (
        "зонд знает только имя файла — значит он снова слеп к константам, и "
        "любой ноль в карте ничего не означает"
    )


def test_the_money_store_has_both_a_writer_and_a_reader(files) -> None:
    """Первый контроль: трата пишется и читается, иначе меряет не то."""
    found = _accessors_for("budget_ledger.jsonl", files)

    assert found["write"], "у бюджетного леджера не нашлось ни одного писателя"
    assert found["read"], "у бюджетного леджера не нашлось ни одного читателя"


def test_the_receipts_store_is_not_silent(files) -> None:
    """Второй контроль: именно здесь зонд молчал из-за невидимого байта.

    Путь квитанций выдаёт функция-резолвер, а не константа в теле, поэтому
    хранилище проверяет РАЗВЁРТКУ имён, а не только их сбор.
    """
    found = _accessors_for("tool_receipts.jsonl", files)

    assert found["write"] and found["read"], (
        "квитанции инструментов снова выглядят нетронутыми — так выглядела "
        "поломка зонда, а не отсутствие обращений"
    )
