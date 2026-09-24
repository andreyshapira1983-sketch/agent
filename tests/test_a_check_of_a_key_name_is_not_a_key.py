"""Проверка, задана ли переменная с ключом, — код, а не утечка ключа.

Ночь 24→25.09, 20:14:48 (trace_a4863d22…): сторож секретов отказал агенту в
записи инструмента из-за строки `if not api_key: return {"error": …}` —
двоеточие условия приняли за присваивание ключа.
"""
from __future__ import annotations

from core.secret_scanner import scan


def test_a_condition_on_the_key_name_is_not_a_secret() -> None:
    line = '        if not api_key: return {"error": "DEEPSEEK_API_KEY is not set"}'
    assert scan(line) == []


def test_real_values_are_still_caught() -> None:
    assert scan("password: hunter2")
    assert scan('api_key = "sk-abc123def456ghi789jkl012mno345pqr678"')
