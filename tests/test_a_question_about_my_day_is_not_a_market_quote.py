"""Вопрос о своей работе за день — не вопрос о биржевой сводке.

Замер 2026-09-21. Оператор спросил агента в панели: «что ты успел сделать
сегодня». `is_realtime_question` вернула True — тот же класс, что «сколько
сейчас стоит биткоин». Слово «сегодня» лежало в `_REALTIME_FRAGMENT_TERMS`
рядом с «биткоин», «погода», «курс», а в список двусмысленных слов
(`_AMBIGUOUS_REALTIME_TERMS`) не попало. Дальше `core/output_policy.py`
ставил потолок уверенности `low` и приклеивал к ответу штамп про
«специализированный live источник с timestamp». Штамп стоял в каждом
ответе агента о его собственной работе, и оператор читал его как
«агент никогда не уверен в себе».

Защита для двусмысленных слов была, но гасила их только при слове
«репозиторий»/«кодовая база» — а человек, спрашивающий агента о его дне,
так не говорит. Вопрос о собственной работе агента («ты», «твой»,
«did you») — такой же местный, как вопрос о репозитории.

Сильные рыночные слова («биткоин», «курс», «цена», «погода») не гасятся
ничем: вопрос о курсе остаётся вопросом о свежих данных, кто бы его ни задал.
"""
from __future__ import annotations

import pytest

from core.source_ranker import is_realtime_question


@pytest.mark.parametrize("question", [
    "что ты успел сделать сегодня",
    "что ты сегодня делал в репозитории",
    "что ты сделал за последние сутки",
    "чем ты сейчас занят",
    "какие файлы ты сегодня правил",
    "расскажи, что было в твоей работе сегодня",
    "what did you do today",
    "what have you changed today in your own code",
])
def test_a_question_about_the_agents_own_day_is_not_realtime(question: str) -> None:
    assert not is_realtime_question(question), question


@pytest.mark.parametrize("question", [
    "сколько сейчас стоит биткоин",
    "какой сегодня курс доллара",
    "какая сегодня погода",
    "ты знаешь, какая цена биткоина сегодня?",
    "какие новости сегодня",
    "latest BTC price today",
    "weather today for the repository owner",
])
def test_market_and_news_questions_stay_realtime(question: str) -> None:
    assert is_realtime_question(question), question
