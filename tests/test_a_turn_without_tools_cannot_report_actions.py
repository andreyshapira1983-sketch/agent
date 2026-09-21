"""Ход без единого инструмента не выпускает доклад о действиях без поправки.

Разговор через мостик 2026-09-21 ~17:10: план дважды не собрался, ни один
инструмент не выполнился, а вывод сказал «записано … и по измерению file_read
в нём 9412 байт». Сверка с журналом была только для записи файлов; «прочитал»,
«по измерению», «проба показала» при нуле инструментов проходили. Замер по 126
ответам мостика: такой доклад — один (этот), ложных срабатываний новой
сверки — ноль (уточнения «cannot be read from the wording», «Помню. Я сам
нашёл…», светская реплика — без поправки).
"""
from __future__ import annotations

from core.answer_contradiction import action_report_mismatch

_ZERO = "ни одного инструмента"


def test_a_measurement_claimed_with_no_tools_is_flagged() -> None:
    note = action_report_mismatch(
        "Три замечания приняты. По измерению file_read в нём 9412 байт.", [])
    assert note and _ZERO in note


def test_reading_and_probe_claims_are_flagged_too() -> None:
    assert _ZERO in (action_report_mismatch("Прочитал core/llm.py целиком.", []) or "")
    assert _ZERO in (action_report_mismatch("Проба показала 5 строк.", []) or "")


def test_the_same_claim_with_tools_is_not_this_warning() -> None:
    assert action_report_mismatch("Прочитал core/llm.py целиком.", ["file_read"]) is None


def test_an_honest_nothing_done_is_not_flagged() -> None:
    assert action_report_mismatch(
        "В этом ходе я не записал ни одного файла и не выполнил ни одной пробы.", []) is None


def test_words_that_only_look_like_actions_are_not_flagged() -> None:
    clarification = ("Уточнение перед выполнением: the request mixes reading and changing "
                     "over several paths; which of them must change cannot be read from the wording")
    assert action_report_mismatch(clarification, []) is None
    assert action_report_mismatch("Помню. Я сам нашёл, что core/step_references.py:186 подставляет ссылку.", []) is None
    assert action_report_mismatch("Привет! Дела идут нормально — я готов помочь.", []) is None


def test_the_report_is_its_first_paragraph_whatever_the_line_endings() -> None:
    """Denial in a later paragraph must not silence the headline (CRLF text)."""
    answer = "По измерению в нём 9412 байт.\r\n\r\n• Я не выполнил ни одной пробы раньше."
    assert _ZERO in (action_report_mismatch(answer, []) or "")
