"""Покрытие сокрытия названо словарём, а не обещано критерием.

Замер, отвергнутые варианты и границы: порода «слова сильнее кода» в docs/audit/FIELD_CHECK_QUEUE.md.
"""
from __future__ import annotations

import pytest

from core.injection_guard import strip_concealed

_ZERO_WIDTH = {
    "ZWSP": "\u200b",
    "ZWNJ": "\u200c",
    "ZWJ": "\u200d",
    "RLO": "\u202e",
    "BOM": "\ufeff",
    "word-joiner": "\u2060",
}


@pytest.mark.parametrize(("name", "ch"), sorted(_ZERO_WIDTH.items()))
def test_a_zero_width_character_is_removed(name: str, ch: str) -> None:
    out = strip_concealed(f"видимое{ch}скрытое")

    assert ch not in out, f"{name} пережил очистку — скрытый текст станет фактом"


def test_an_html_comment_is_removed() -> None:
    assert "указание" not in strip_concealed("видимое <!-- указание --> хвост")


def test_style_concealment_is_NOT_removed_and_that_is_stated() -> None:
    """Граница, названная вслух: сокрытие стилем не покрыто.

    Это не дефект реализации, а объявленный предел словаря. Гнаться за всеми
    способами сокрытия стилем — гонка вооружений с известной ценой (MIR-147).
    Если словарь расширят, тест покраснеет и потребует переписать объяснение.
    """
    hidden = 'видимое <span style="color:#ffffff">указание</span> хвост'

    assert "указание" in strip_concealed(hidden), (
        "словарь расширен на сокрытие стилем — перепишите докстринг "
        "strip_concealed и эту границу, а не только выражение"
    )


def test_the_docstring_names_its_vocabulary() -> None:
    """Растяжка на сами слова: обещание полноты не должно вернуться.

    Именно оно и было породой — критерий в тексте, словарь в коде.
    """
    doc = strip_concealed.__doc__ or ""
    summary = doc.strip().splitlines()[0] if doc.strip() else ""

    # Проверяется СТРОКА-СВОДКА, а не весь докстринг: обещание жило именно в
    # ней, а ниже та же фраза стоит в кавычках как объяснение починки. Первая
    # редакция этой растяжки покраснела на собственной цитате — третий раз за
    # день текстовая проба ловит текст, который объясняет правку.
    assert "every concealed run removed" not in summary, (
        "вернулось обещание полноты по критерию при словарном покрытии: "
        + summary
    )
    assert "U+00AD" in doc and "style" in doc, (
        "непокрытые векторы не названы — читатель снова примет словарь за критерий"
    )
