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

_REAL_DUPLICATES = [
    ("Ключ OpenAI работает", "Ключ OpenAI работает."),
    ("Тесты зелёные", "тесты зелёные"),
    ("Лента коммитит локально", "лента коммитит локально"),
]


@pytest.mark.parametrize(("a", "b"), _ROLE_SWAPS)
def test_a_role_swap_is_not_merged_away(a: str, b: str) -> None:
    score = _similarity(a, b)
    assert score < DEFAULT_DEDUP_THRESHOLD, (
        f"перестановка ролей читается дубликатом ({score:.2f}) — поправка, "
        f"меняющая роли местами, не попадёт в память"
    )


@pytest.mark.parametrize(("a", "b"), _REAL_DUPLICATES)
def test_a_real_duplicate_is_still_merged(a: str, b: str) -> None:
    """Контроль: без него правило можно было бы «починить», сломав дедупликацию."""
    score = _similarity(a, b)
    assert score >= DEFAULT_DEDUP_THRESHOLD, (
        f"настоящий дубликат перестал сливаться ({score:.2f}) — цена починки "
        f"выше самой починки"
    )


def test_identical_text_still_reads_one() -> None:
    """Граница: тождество не должно зависеть ни от какой новой ветки."""
    assert _similarity("одно и то же", "одно и то же") == 1.0


def test_the_write_gate_actually_lets_the_correction_through(tmp_path) -> None:
    """Проводка: важна не оценка, а то, что поправку ЗАПИШУТ.

    Тест на функцию схожести зелен и тогда, когда ворота записи её не зовут;
    этот урок за сегодня повторился пять раз, поэтому проверяется само решение.
    """
    from core.memory_hygiene import find_duplicate

    class _Rec:
        id = "m1"
        content = "openai падает, anthropic работает"

    match = find_duplicate(
        "anthropic падает, openai работает", [_Rec()],
    )
    assert match is None, (
        "ворота записи по-прежнему видят дубликат: поправка будет отвергнута, "
        f"а память сохранит устаревшее — {match}"
    )

def test_the_echo_antibody_still_catches_a_reordered_restatement() -> None:
    """Граница, найденная столкновением с чужим тестом, а не выдуманная.

    Правка сначала была общей и уронила `test_semantic_duplicate_is_rejected`.
    Тот тест прав для СВОЕЙ цели: антитело ловит, как агент повторяет сам себя,
    и пересказ теми же словами в другом порядке — это эхо. У двух потребителей
    одной меры противоположная цена ошибки, и разведены они теперь явным
    параметром, а не подгонкой чужого теста под свой.
    """
    from core.memory_echo_antibody import DEFAULT_ECHO_THRESHOLD
    from core.memory_hygiene import _similarity as sim

    a = "agent keeps retrying the failed deploy step"
    b = "the failed deploy step keeps retrying agent"

    assert sim(a, b, order_sensitive=False) >= DEFAULT_ECHO_THRESHOLD, (
        "антитело перестало видеть эхо — петля повторов снова открыта"
    )
    assert sim(a, b) < DEFAULT_DEDUP_THRESHOLD, (
        "ворота записи снова считают перестановку дубликатом"
    )
