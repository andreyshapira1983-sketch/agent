"""Отказ на воротах — факт о мире, и его надо помнить как отказ.

Замер 2026-09-19 по data/campaign_ledger.jsonl: 170 циклов подряд с исходом
`blocked` и одним и тем же действием `improve_failure_to_idea_pipeline`.
Двадцать одинаковых подряд, девять часов, ноль вызовов модели, ноль
продукта. Круг замкнут: урок о неприменённой самоправке жив, пока не
случится успешного применения; применение упирается в ворота; значит урок
остаётся, и следующий цикл предлагает то же самое.

Почему это не ловилось. Подпись действия банится только когда `outcome.ran`,
а отказ до старта — не попытка, и это написано в типах намеренно:

    «банить его подпись значило бы лгать „прежний проход не снял сигнал“»

Рассуждение верное. Ошибка в выводе: из «нельзя сказать, что была попытка»
сделали «не говорить ничего». Отказ на воротах — самостоятельный факт: этот
путь сейчас закрыт. Он помнится ОТДЕЛЬНО от попыток и своими словами, и
повторно предлагать действие в той же серии циклов не даёт. Смена цели или
пробуждение память об отказе очищают вместе с прочей — мир изменился, можно
пробовать снова.
"""
from __future__ import annotations

from core.campaign import _refusal_reason, _refused_again


def test_a_refusal_speaks_its_own_words() -> None:
    said = _refusal_reason("improve_failure_to_idea_pipeline")
    assert "ворота" in said
    assert "прежний проход" not in said, "о попытке, которой не было, не врём"


def test_the_second_offer_of_a_refused_action_is_skipped() -> None:
    refused: set[str] = set()
    assert not _refused_again("improve_failure_to_idea_pipeline", refused, ran=False,
                              result="blocked")
    assert _refused_again("improve_failure_to_idea_pipeline", refused, ran=False,
                          result="blocked")


def test_a_refusal_that_spent_something_is_not_a_gate_refusal() -> None:
    """Потраченный вызов — это попытка; её судят прежние правила."""
    refused: set[str] = set()
    assert not _refused_again("x", refused, ran=True, result="blocked")
    assert not _refused_again("x", refused, ran=True, result="blocked")
    assert refused == set()


def test_other_outcomes_are_not_refusals() -> None:
    refused: set[str] = set()
    for result in ("idle", "completed", "failed", "empty"):
        assert not _refused_again("y", refused, ran=False, result=result)
    assert refused == set()


def test_a_different_action_is_unaffected() -> None:
    refused: set[str] = set()
    _refused_again("a", refused, ran=False, result="blocked")
    assert not _refused_again("b", refused, ran=False, result="blocked")
