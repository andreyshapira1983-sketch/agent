"""Запись не выигрывает отбор памяти своим размером.

Замер 2026-09-23 на живом хранилище: нужный урок всплывал 5 раз из 9 на
памяти до 14:30 и 0 из 9 к вечеру. Балл записи был ЧИСЛОМ ОБЩИХ СЛОВ с
вопросом, без поправки на длину и на редкость слова, а в канале уроков по
роду работы побеждала просто самая свежая запись. Вечером в хранилище легли
разборы по 2400 знаков против обычных 770 — и заняли оба канала на любой
вопрос.

Сделано как в литературе, а не придумано:
- канал по вопросу ранжирует по BM25 (Robertson & Zaragoza; k1 = 1.2,
  b = 0.75, IDF в форме Lucene): частота слова делится на длину записи
  относительно средней, редкое слово весит больше частого;
- канал уроков ранжирует по сумме нормированных релевантности и свежести,
  как у Generative Agents (Park et al. 2023, arXiv 2304.03442).

Порог допуска (`min_score`) не тронут: кто проходил, тот проходит.

После правки на том же замере: 0 из 9 -> 3 из 9. Разбор по частям: BM25 не
ухудшил ни на одном срезе памяти; вторая часть +1 на нынешней памяти и -1 на
утреннем срезе, где искомые уроки сами были самыми свежими. На девяти
вопросах разница в одно попадание — шум; больше отсюда не вывести.
"""
from __future__ import annotations

import datetime as dt

from core.memory_policy import MemoryRetrievalPolicy
from core.models import MemoryRecord


def _record(content: str, *, hours_ago: float = 0.0) -> MemoryRecord:
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return MemoryRecord(
        content=content, type="semantic", tags=["fact"],
        owner="self", source="agent-auto",
        created_at=stamp.isoformat(),
    )


def _only_question_channel(max_records: int = 1) -> MemoryRetrievalPolicy:
    policy = MemoryRetrievalPolicy(max_records=max_records)
    policy.lessons_by_kind = 0
    return policy


def test_a_long_record_does_not_win_by_its_size() -> None:
    """Те же три слова вопроса, но одна запись в сто раз длиннее.

    Раньше счёт был равный (три общих слова), и ничью отдавали более свежей —
    длинной. Короткая запись, где эти слова и есть содержание, должна идти
    первой.
    """
    short = _record("Кэш запроса держится, пока шапка не меняется.", hours_ago=24)
    filler = " ".join(f"посторонний{i}" for i in range(300))
    long = _record(f"Кэш запроса и шапка упомянуты мимоходом. {filler}", hours_ago=0)

    chosen = _only_question_channel().select([short, long], "кэш запроса шапка")

    assert chosen and chosen[0] is short, "длинная запись выиграла отбор размером"


def test_a_rare_word_outweighs_a_common_one() -> None:
    """Слово, которое есть почти в каждой записи, почти ничего не различает."""
    common = [_record(f"Запрос обработан полностью, номер {w}.", hours_ago=0)
              for w in ("первый", "второй", "третий", "четвёртый", "пятый")]
    rare = _record("Кэш прогрет полностью заново.", hours_ago=24)

    chosen = _only_question_channel().select([*common, rare], "кэш запрос")

    assert chosen and chosen[0] is rare, "частое слово перевесило редкое"


def test_the_lesson_that_fits_the_question_beats_a_fresher_one() -> None:
    """Канал уроков: урок по делу важнее свежей записи не по делу.

    Оба — род работы «замер». Раньше брался самый свежий, какой бы ни был
    вопрос. Двух кандидатов достаточно, чтобы проверить и ничью: нормировка
    даёт одному 1 + 0, другому 0 + 1, и решать её должна релевантность.
    """
    lesson = _record(
        "Прежде чем мерить, проверь, что мера касается правки: замер до и после.",
        hours_ago=48,
    )
    fresh = _record("Замер расхода интернета за сутки: сто девяносто чтений.", hours_ago=0)
    policy = MemoryRetrievalPolicy(max_records=0)
    policy.lessons_by_kind = 1

    chosen = policy.select([lesson, fresh], "измерь, стало ли лучше после правки")

    assert chosen == [lesson], "свежая запись не по делу заняла место урока"


def test_between_equally_unrelated_lessons_the_fresher_wins() -> None:
    """Свежесть не выброшена: при равной релевантности решает она.

    Устаревший урок бьёт по трудным задачам сильнее, чем помогает (замерено
    раньше: минус 26%), поэтому свежесть осталась половиной балла.
    """
    old = _record("Замер памяти: сто девяносто записей в хранилище.", hours_ago=72)
    new = _record("Замер расхода: сорок вызовов модели за час.", hours_ago=1)
    policy = MemoryRetrievalPolicy(max_records=0)
    policy.lessons_by_kind = 1

    chosen = policy.select([old, new], "измерь, стало ли лучше")

    assert chosen == [new]


def test_without_relevance_the_lesson_channel_keeps_the_old_order() -> None:
    """Прежний вызов без релевантности даёт прежний порядок — самые свежие."""
    older = _record("Замер один: проба до правки.", hours_ago=10)
    newer = _record("Замер два: проба после правки.", hours_ago=1)
    policy = MemoryRetrievalPolicy()
    policy.lessons_by_kind = 1

    chosen = policy._add_lessons_by_work_kind([], [older, newer], frozenset({"measure"}))

    assert chosen == [newer]
