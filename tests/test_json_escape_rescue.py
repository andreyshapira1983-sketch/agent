r"""Один неэкранированный слэш не должен обнулять работу на 183 единицы бюджета.

Живой тик 2026-08-04. Строитель ответил 46 057 символами (20 509 токенов,
183 единицы, 170 секунд), предложив разрезать большой модуль на подмодуль —
ровно ту работу, которую от него и ждут. Ответ отвергли с формулировкой
«builder reply did not parse into usable content», кандидат выброшен.

Разбор сохранённого сырца (его удержал MIR-071): JSON завершён корректно, а
невалиден из-за ОДНОГО символа — одиночного `\` перед пробелом на позиции
32326, продолжение строки в стиле кода внутри JSON-строки. После
экранирования этого слэша объект разбирается целиком: два файла,
confidence 0.85 — выше порога 0.60, то есть кандидат прошёл бы.

Здесь закрепляется спасательный проход: обычный разбор не тронут, починка
применяется только когда все прочие попытки уже провалились.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plan_parsing import extract_json_object

#: Минимальный воспроизводитель живого случая: одиночный слэш перед пробелом.
LIVE_SHAPE = (
    '{"files": [{"path": "a.py", "content": "x = 1  # path\\ continues"}], '
    '"confidence": 0.85}'
)


def test_the_live_shape_is_rescued():
    obj = extract_json_object(LIVE_SHAPE)

    assert obj is not None, "ответ отвергнут из-за одного слэша"
    assert obj["confidence"] == 0.85
    assert obj["files"][0]["path"] == "a.py"
    assert "continues" in obj["files"][0]["content"], "содержимое потеряно при спасении"


def test_a_plain_reply_is_untouched():
    """Спасение — последний рубеж: здоровый ответ разбирается как раньше."""
    obj = extract_json_object('{"a": "b\\nc", "n": 1}')

    assert obj == {"a": "b\nc", "n": 1}


def test_valid_escapes_survive_the_rescue():
    """Починка не смеет портить уже правильные последовательности."""
    text = (
        '{"kept": "keep \\\\ this", "tab": "a\\tb", '
        '"quote": "say \\"hi\\"", "oops": "p\\ q"}'
    )
    obj = extract_json_object(text)

    assert obj is not None
    assert obj["tab"] == "a\tb"
    assert obj["quote"] == 'say "hi"'
    assert obj["kept"] == "keep \\ this", "валидное экранирование испорчено починкой"


def test_genuinely_broken_json_still_fails():
    """Спасение не должно превращать мусор в объект."""
    assert extract_json_object('{"unclosed": "value') is None
    assert extract_json_object("не json вовсе") is None


def test_rescued_content_matches_a_manual_repair():
    """Спасённый объект совпадает с тем, что даёт ручное экранирование."""
    manual = json.loads(LIVE_SHAPE.replace("path\\ ", "path\\\\ "))

    assert extract_json_object(LIVE_SHAPE) == manual


def test_the_actual_rejected_reply_parses_if_it_is_still_on_disk():
    """Тот самый ответ строителя, если сырец ещё лежит в logs/.

    Файлы отклонённых ответов не вечны, поэтому проверка мягкая: пропускаем,
    когда их нет. Но пока сырец на месте — он стережёт живой случай целиком,
    а не его уменьшенную копию.
    """
    rejects = sorted(Path("logs/self_build_rejects").glob("reject_*.txt"))
    if not rejects:
        pytest.skip("сырцов отклонённых ответов на диске нет")

    parsed = [extract_json_object(p.read_text(encoding="utf-8")) for p in rejects]

    assert any(obj is not None for obj in parsed), (
        "ни один сохранённый ответ строителя не разбирается — спасение не работает"
    )
