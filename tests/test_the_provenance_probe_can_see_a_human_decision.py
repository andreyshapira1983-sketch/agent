"""Ноль в переписи происхождения прав что-то значит только с контролем.

Замер, отвергнутые варианты и границы: MIR-155 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import pytest

from scripts.authority_provenance import (
    _HAND_REJECTED,
    AUTHORITIES,
    _entries_naming_a_human_decision,
    introducing_commit,
)


def test_the_detector_finds_a_recorded_human_decision() -> None:
    """Положительный контроль: «0 из 14» иначе означало бы сломанный зонд."""
    found = _entries_naming_a_human_decision()

    assert found, (
        "определитель не нашёл НИ ОДНОЙ записи с названным решением человека — "
        "значит он не умеет их находить, и любой ноль в переписи пуст"
    )
    assert "MIR-139" in found, (
        "запись, где решение оператора названо прямо, не распознана"
    )


def test_a_right_from_the_initial_lump_is_marked_as_such() -> None:
    """Граница: у права из начального коммита происхождения нет, и это видно.

    Один токен, а не все четырнадцать: полный обход стоит 43 секунды, потому
    что `git log -S` идёт по всей истории на каждый токен.
    """
    import subprocess

    roots = subprocess.run(["git", "log", "--max-parents=0", "--format=%s"], capture_output=True,
                           text=True, check=False).stdout.split("\n")
    if "Initial commit" not in (r.strip() for r in roots):
        # Замер делается на истории проекта; у локальной копии без неё
        # (архив, заново инициализированный git) начального коммита нет.
        pytest.skip("история репозитория — не история проекта (нет «Initial commit»)")
    found = introducing_commit(AUTHORITIES["budget_ledger"])

    assert found is not None
    _hash, _date, subject = found
    assert subject.strip() == "Initial commit"


def test_the_hand_verdict_is_carried_by_the_tool_itself() -> None:
    """Ручная выборка обязана жить в инструменте, а не только в отчёте.

    Иначе перезапуск печатал бы машинное попадание, которое человек уже
    отверг, и число снова разошлось бы с правдой.
    """
    assert "procedural_memory" in _HAND_REJECTED
    assert "MIR-044" in _HAND_REJECTED["procedural_memory"]
