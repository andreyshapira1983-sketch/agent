"""Предупреждения проверки доходят до человека словами, а не машинными метками.

Строки — из ответов агента в чате оператора 24.09 (15:59, 16:18, 18:25).
Предупреждение не исчезает (граница H-43), а переводится у самого утверждения.
"""
from __future__ import annotations

from core.answer_format import format_human_response
from core.warning_words import humanize_warning_markers

LIVE_1559 = ("• При 240–300 часах работы эффективная ставка $16,67–$20,83/час "
             "[topic-only:tool:python_probe]. [claim-figure-unverified]")
LIVE_1618 = ("• По данным Upwatcher (май 2026), медианная ставка — около $30/ч "
             "[topic-only:web:https://www.upwatcher.io/market/ai/]. [claim-figure-unverified]")
LIVE_1825 = "• Файл data/lesson_injections.jsonl не существует. [absence-unverifiable]"


def test_no_machine_marker_is_left_for_the_human() -> None:
    for line in (LIVE_1559, LIVE_1618, LIVE_1825):
        shown = humanize_warning_markers(line)
        assert "[topic-only" not in shown and "[claim-" not in shown and "[absence-" not in shown, shown


def test_the_warning_itself_survives_in_words() -> None:
    shown = humanize_warning_markers(LIVE_1559)
    assert "не подтверждает" in shown and "число не сверено" in shown
    assert "проверить нельзя" in humanize_warning_markers(LIVE_1825)


def test_the_source_address_is_kept() -> None:
    assert "https://www.upwatcher.io/market/ai/" in humanize_warning_markers(LIVE_1618)


def test_an_english_answer_gets_english_words() -> None:
    shown = humanize_warning_markers("The median rate is $26/h [claim-figure-unverified].")
    assert "number not checked" in shown and "[" not in shown


def test_the_display_edge_translates_both_kinds_of_answer() -> None:
    contract = f"Conclusion: ставка низкая.\nFacts:\n- {LIVE_1559}\nUnverified: nothing\n"
    for answer in (contract, LIVE_1559):
        assert "[claim-figure-unverified]" not in format_human_response(answer)
