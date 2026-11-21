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
