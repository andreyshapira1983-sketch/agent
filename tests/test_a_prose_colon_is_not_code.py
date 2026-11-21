"""Двоеточие в русской фразе — не код (часть 2 проверки А–Я, 24.09).

Дверь памяти (`memory_bank`) отказывала «text looks like code» короткому
выводу с двоеточием: «DeepSeek 24.09 отдаёт две модели: deepseek-flash и
deepseek-v4-pro.» Правило считало кодом ЛЮБОЕ двоеточие или скобку при ≤8
словах, а двоеточие после обобщения — норма прозы. Код по-прежнему отказ.
"""
from __future__ import annotations

import pytest

from core.persistent_memory import _looks_like_code


@pytest.mark.parametrize("prose", [
    "DeepSeek 24.09 отдаёт две модели: deepseek-flash и deepseek-v4-pro.",
    "Проверка инструмента 24.09: в копии toolcheck.",
    "Итог: на скане нет строки «Доставка» (1 500 руб.).",
])
def test_a_short_sentence_with_a_colon_is_prose(prose: str) -> None:
    assert not _looks_like_code(prose)


@pytest.mark.parametrize("code", [
    "if signature in attempted_signatures:",
    "x = foo(bar)",
    "def f(): return 1",
    "{'a': 1, 'b': 2}",
    "import os; os.remove(p);",
    "result[key] -> value",
])
def test_code_is_still_code(code: str) -> None:
    assert _looks_like_code(code)
