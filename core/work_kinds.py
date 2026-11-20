"""Work kind: what the person is doing now, not the words used; a closed classifier shared by memory retrieval (`MemoryRetrievalPolicy`) and workflow memory (core/workflow_memory.py)."""
from __future__ import annotations

import re

#: WORK KIND: what the person is doing now, not the words used. The vocabulary is deliberately small and closed; it ADDS to word matching rather than replacing it
#: (a hint, not a verdict; a record without a kind loses nothing).
_WORK_KINDS: dict[str, tuple[str, ...]] = {
    # code edits and defect repair
    "patch": (
        "правк", "почин", "чини", "исправ", "дефект", "поломк", "ошибк",
        "баг", "коммит", "тест", "регресс", "сломал", "упал",
    ),
    # measuring, comparison, 'did it get better'
    "measure": (
        "замер", "измер", "мера", "мерит", "сравн", "проб", "лучше", "хуже",
        "процент", "среднее", "распредел", "статист", "выборк",
    ),
    # money, orders, spending
    "money": (
        "доллар", "валют", "сумм", "цена", "стоит", "стоим", "заказ",
        "ставк", "расход", "бюджет", "оплат", "счёт", "тариф", "окупа",
    ),
    # installing tools for itself
    "install": (
        "пакет", "библиотек", "постав", "установ", "окружен", "зависимост",
        "интерпрет", "версия", "ввоз", "импорт",
    ),
    # talking with a person
    "speak": (
        "оператор", "человек", "сказать", "спросить", "застря", "упёр",
        "обращ", "внимани", "голос", "сообщ", "написать ему",
    ),
    # evidence, sources, fact checking
    "source": (
        "источник", "улик", "ссылк", "цитат", "подтвержд", "провер",
        "факт", "утвержд", "сверк",
    ),
}


#: A marker must START a word, not hide inside another (substring matching tagged nearly everything as 'money': 'цена' inside 'оценка', 'счёт' inside 'счётчик').
_WORK_KIND_RE: dict[str, re.Pattern[str]] = {
    kind: re.compile(
        r"(?<![0-9а-яёa-z])(?:" + "|".join(markers) + r")",
        re.IGNORECASE,
    )
    for kind, markers in _WORK_KINDS.items()
}


def work_kinds(text: str) -> frozenset[str]:
    """Work kinds this text resembles."""
    lowered = (text or "").lower()
    if not lowered:
        return frozenset()
    return frozenset(
        kind for kind, pattern in _WORK_KIND_RE.items()
        if pattern.search(lowered)
    )
