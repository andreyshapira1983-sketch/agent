"""Граница метода — не противоречие; верный опыт не выбрасывается за честность.

Замер 2026-09-19, внешний экзамен: одна задача (суммы по именам → report.json)
десять раз подряд. Девять ответов получили self_contradiction, ни один эпизод
не стал опытом — агент ни разу не увидел свой прошлый запуск. Все девять
оговорок были границей МЕТОДА («содержимое не перечитано — подтверждён только
факт записи (87 байт)»), а не снятием факта. Две из них сработали на ложных
предметах: дата из имени бэкапа читалась как SHA, `report.json` — как предмет
внутри `report.json.bak.…`.
"""
from __future__ import annotations

import pytest

from core.answer_contradiction import contradicted_claims

_FACTS = (
    "Conclusion: `report.json` создан, суммы по каждому name посчитаны.\n"
    "Facts:\n"
    "- Файл `report.json` записан в режиме create, записано 87 байт [file_write:report.json].\n"
    "- Бэкап report.json.bak.20260919T014750Z и план отката comp_cdb4b9d9ac.\n"
    "Sources:\n1. file_write:report.json - запись\n"
    "Confidence: medium\n"
    "Unverified:\n"
)


@pytest.mark.parametrize("caveat", [
    "- Содержимое `report.json` прочитано не было — подтверждён только факт записи (87 байт).",
    "- Содержимое `report.json` не перечитано после записи: подтверждён факт записи (87 байт).",
    "- Содержимое самого файла `report.json` на диске я не перечитывал.",
    "- Содержимое `report.json` не прочитано обратно из файла.",
    "- Кодировка `report.json` (UTF-8) не проверена отдельно.",
    "- Точное содержимое `report.json` не подтверждено отдельным чтением; известны только факт записи и размер (87 байт).",
    "- Не проверено, что `report.json` побайтово совпадает с JSON: подтверждены только факт создания и размер 87 байт.",
    "- Область доказательств ограничена `data.csv`; содержимое `report.json` как прочитанного файла не проверялось.",
    "- Соответствие записанных 87 байт выведенному JSON подтверждается только косвенно.",
    "- Не удалось прочитать файл `report.json` при повторной попытке: инструмент вернул ошибку.",
])
def test_a_method_boundary_is_not_a_denial(caveat: str) -> None:
    """Каждая из живых оговорок — отдельно; все они из ответов, где файл верен."""
    assert contradicted_claims(_FACTS + caveat + "\n") == ()


def test_a_date_is_not_a_sha_and_a_backup_name_is_not_the_file() -> None:
    answer = _FACTS + "- Существование report.json.bak.20260919T014750Z и успешность отката.\n"
    assert contradicted_claims(answer) == ()


def test_retracting_the_same_file_is_still_caught() -> None:
    """ПРЕДОХРАНИТЕЛЬ: снятие без оговорки о методе — по-прежнему противоречие."""
    answer = _FACTS + "- Не доказано, что `report.json` создан; запись могла не состояться.\n"
    assert [c.subject for c in contradicted_claims(answer)] == ["report.json"]


def test_a_real_sha_is_still_a_subject() -> None:
    answer = (
        "Facts:\n- Дерево чисто на 20698b1e4c2. [shell]\n"
        "Unverified:\n- Не доказано, что 20698b1e4c2 — коммит этого прогона.\n"
    )
    assert [c.subject for c in contradicted_claims(answer)] == ["20698b1e4c2"]
