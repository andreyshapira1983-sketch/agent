"""Показание журнала хода доходит до человека, а не теряется при пересборке.

2026-09-22 14:10: детектор расхождения сработал («⚠️ По журналу хода: в этом
ходе НЕ выполнено ни одной записи файла»), строка легла в собранный ответ —
2518 знаков, — а человеку ушло 1521: `format_human_response` пересобирает
ответ по разделам и всё прочее выбрасывает. Доклад «правка записана» уходил
без поправки, и файла действительно не было.
"""
from __future__ import annotations

from core.answer_format import format_human_response

_NOTICE = ("⚠️ По журналу хода: в этом ходе НЕ выполнено ни одной записи файла "
           "(file_write: 0) — утверждение о записи в выводе не подтверждено.")
_ANSWER = f"""Conclusion:
Правка записана в proposals/selffix/lesson_bank/edits.txt.
Facts:
- файл содержит два блока
{_NOTICE}
Проверка: подтверждено 1 из 2 утверждений; уверенность: средняя"""


def test_the_notice_survives_the_rewrite() -> None:
    out = format_human_response(_ANSWER)
    assert _NOTICE in out
    assert out.index(_NOTICE) > out.index("Правка записана"), "поправка стоит после доклада"


def test_the_notice_comes_before_the_verification_tail() -> None:
    out = format_human_response(_ANSWER)
    assert out.index(_NOTICE) < out.index("Проверка: подтверждено")


def test_an_answer_without_a_notice_is_unchanged_in_shape() -> None:
    plain = "Conclusion:\nВсё сделано.\nFacts:\n- раз"
    assert "По журналу хода" not in format_human_response(plain)
