"""Те же слова в другом порядке — не обязательно тот же факт.

Замер, отвергнутые варианты и границы: F-2 в docs/audit/FIELD_CHECK_QUEUE.md.
"""
from __future__ import annotations

import pytest

from core.memory_hygiene import DEFAULT_DEDUP_THRESHOLD, _similarity

_ROLE_SWAPS = [
    ("openai падает, anthropic работает", "anthropic падает, openai работает"),
    ("лимит вырос с 3000 до 5000", "лимит вырос с 5000 до 3000"),
    ("ключ A истёк, ключ B живой", "ключ B истёк, ключ A живой"),
]


@pytest.mark.parametrize(("a", "b"), _ROLE_SWAPS)
def test_a_role_swap_is_not_merged_away(a: str, b: str) -> None:
    score = _similarity(a, b)
    assert score < DEFAULT_DEDUP_THRESHOLD, (
        f"перестановка ролей читается дубликатом ({score:.2f}) — поправка, "
        f"меняющая роли местами, не попадёт в память"
    )
