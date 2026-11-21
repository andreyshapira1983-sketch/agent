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


def test_retracting_the_same_file_is_still_caught() -> None:
    """ПРЕДОХРАНИТЕЛЬ: снятие без оговорки о методе — по-прежнему противоречие."""
    answer = _FACTS + "- Не доказано, что `report.json` создан; запись могла не состояться.\n"
    assert [c.subject for c in contradicted_claims(answer)] == ["report.json"]
