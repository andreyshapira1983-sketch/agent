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


def test_the_probe_source_carries_no_control_bytes() -> None:
    """Ломка, которую нельзя увидеть глазами: 0x08 вместо двух знаков границы.

    Управляющий байт в выражении не печатается терминалом: код читается верным
    и не совпадает никогда. Проверять приходится байтами.
    """
    import scripts.memory_authority_map as probe

    # Адрес берётся у самого модуля: буквальный путь к боевому файлу в тесте
    # запрещён (`test_no_test_pins_a_production_path`) и ломается при переезде.
    src = pathlib.Path(probe.__file__).read_text(encoding="utf-8")

    bad = {c for c in src if ord(c) < 32 and c not in "\n\t"}

    assert not bad, f"в исходнике зонда управляющие байты: {[hex(ord(c)) for c in bad]}"
