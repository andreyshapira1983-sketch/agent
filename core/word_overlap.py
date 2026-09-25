"""Грубое сходство двух текстов: набор слов и доля общих (мера Жаккара).

Одна копия на нескольких потребителей. Раньше `core/charter_goal.py` и
`core/causal_claim_store.py` держали один и тот же разбор на слова, а
`core/conversation_contract.py` и `core/referent_resolver.py` — одну и ту же
меру Жаккара, каждый свою копию.

Это НЕ токенизатор памяти: у памяти свои токенизаторы с порогами и
стоп-словами (`core/topic_tokens.py`, MIR-008), и сюда они не сводятся.
"""
from __future__ import annotations

from collections.abc import Set as AbstractSet


def word_set(text: str) -> frozenset[str]:
    """Слова длиннее двух знаков, в нижнем регистре; разделяет всё, что не буква и не цифра."""
    return frozenset(
        w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split()
        if len(w) > 2
    )


def jaccard(a: AbstractSet[str], b: AbstractSet[str]) -> float:
    """Доля общих слов; пустой набор с любой стороны — 0.0."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
