"""A date range is not a claimed count (work order 1, pass 2, 2026-09-05).

The verifier's R2 rule compares a claimed count with the sentence's own
enumeration. On the Conclusion «…на 15–25 сентября 2026 (1 взрослый, эконом,
только ручная кладь): ни один из трёх проверенных источников (Google
Flights/Expedia, Skyscanner/Trip.com, KAYAK/Kiwi.com)…» it read «25» as a
claimed count against three items and refuted a correct sentence
(count_mismatch expected=25 actual=3). Pinned here on the original
sentence, with negative controls: a real count mismatch is still caught.
"""
from __future__ import annotations

from core.verifier_utils import enumeration_count_reason

SENTENCE = (
    "Ни один из трёх проверенных источников (Google Flights/Expedia, Skyscanner/Trip.com, "
    "KAYAK/Kiwi.com) не предоставил проверяемых тарифов по маршруту Тель-Авив → Берлин на "
    "15–25 сентября 2026 (1 взрослый, эконом, только ручная кладь): все живые запросы были "
    "заблокированы или вернули пустые страницы, поэтому подтверждённых вариантов с полной ценой "
    "и длительностью нет."
)


def test_the_original_sentence_is_not_refuted():
    assert enumeration_count_reason(SENTENCE) is None


def test_dates_in_several_shapes_are_not_counts():
    for text in (
        "вылет 15 сентября 2026 (TLV, BER, один взрослый)",
        "on 25 September 2026 (one adult, economy, hand luggage only)",
        "с 15–25 сентября (три источника, четыре сайта, пять вариантов)",
        "в 10:30 (первый, второй, третий) рейс",
    ):
        assert enumeration_count_reason(text) is None, text


def test_a_real_count_mismatch_is_still_caught():
    reason = enumeration_count_reason("Проверены три источника (Google Flights, Skyscanner, KAYAK, Kiwi).")
    assert reason is not None and reason.code == "count_mismatch"
    reason = enumeration_count_reason("Проверены 3 источника (Google Flights, Skyscanner, KAYAK, Kiwi).")
    assert reason is not None and reason.expected == "3" and reason.actual == "4"
