"""Урок всплывает по РОДУ РАБОТЫ, а не по словам вопроса.

Задача №1 субботнего списка. Замер 2026-09-22: «0 нужных уроков из 4 при
„напиши правку“». Перемерено 2026-09-23 на 186 живых записях по шести
вопросам о работе: нужный урок всплывал **2 раза из 9 (22%)**, после этой
правки — **5 из 9 (56%)**.

Почему не хватало слов. «Поставь библиотеку» не доставало урок «ставя себе
пакет, проверь окружение»: библиотека и пакет — разные слова об одном деле.
«Посчитай, сколько стоит заказ» не доставало урок о написании денежных чисел.

Почему отдельный канал, а не прибавка к баллу. Первая редакция давала +2
КАЖДОЙ записи того же рода, а их в хранилище десятки: ранжирование начинали
решать ничьи по времени, и нужный урок оставался там же, где был (замерено:
44% против 22% — вдвое, но половина по-прежнему не всплывала). Отбор по
вопросу и напоминание по роду работы — две РАЗНЫЕ задачи, и делить между ними
три места значит не решать ни одной.

Размер канала выбран замером, а не на глаз: 1 место — 44%, 2 — 56%,
3 и 4 — те же 56%. Лишние места стоят токенов и не приносят ничего.
"""
from __future__ import annotations

import datetime as dt

from core.memory_policy import MemoryRetrievalPolicy, work_kinds
from core.models import MemoryRecord


def _record(content: str, *, days_ago: int = 0) -> MemoryRecord:
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    return MemoryRecord(
        content=content, type="semantic", tags=["fact"],
        owner="self", source="agent-auto",
        created_at=stamp.isoformat(),
    )


def test_a_marker_must_start_a_word() -> None:
    """«цена» внутри «оценка» — не деньги.

    Первая редакция искала подстроку, и род «деньги» получили почти все
    записи: «цена» лежит внутри «оценка», «счёт» внутри «счётчик».
    Подсказка, срабатывающая на всё, не отличает ничего.
    """
    assert "money" not in work_kinds("оценка качества ответа")
    assert "money" in work_kinds("цена заказа в долларах")


def test_different_words_for_one_kind_of_work_meet() -> None:
    """«библиотека» и «пакет» — одно дело, названное по-разному."""
    assert work_kinds("поставь библиотеку") & work_kinds("ставя себе пакет")
    assert work_kinds("почини дефект") & work_kinds("правка сломала тест")


def test_a_lesson_of_the_same_kind_surfaces_without_shared_words() -> None:
    """Главное свойство: общих слов нет, а урок всплывает."""
    lesson = _record(
        "Ставя себе пакет, проверь, в какое окружение он ложится: "
        "интерпретатор системы и рабочий — разные."
    )
    noise = [
        _record("Вопрос: сколько стоит перевод страницы? Вывод: по-разному.", days_ago=1),
        _record("Теорема о среднем значении доказана численно.", days_ago=2),
        _record("Погода в Флориде не влияет на работу сервера.", days_ago=3),
    ]
    selected = MemoryRetrievalPolicy().select_with_report(
        noise + [lesson], "поставь библиотеку, которой не хватает",
    ).selected
    assert any("окружение" in (r.content or "") for r in selected)


def test_a_transcript_never_rides_the_work_kind_channel() -> None:
    """Слепок обмена отвечает «что однажды спросили», а не «как это делается».

    Их в живом хранилище больше половины (98 из 186 на 2026-09-23), и без
    этого отсечения канал наполнился бы ими.
    """
    transcript = _record(
        "Вопрос: поставь библиотеку pandas. Вывод: поставил pandas в окружение.",
    )
    selected = MemoryRetrievalPolicy().select_with_report(
        [transcript], "установи пакет в окружение",
    ).selected
    # Совпадение слов может поднять слепок — но НЕ канал рода работы.
    policy = MemoryRetrievalPolicy()
    extra = policy._add_lessons_by_work_kind([], [transcript], work_kinds("установи пакет"))
    assert extra == []
    assert len(selected) <= policy.max_records


def test_a_question_without_a_work_kind_changes_nothing() -> None:
    """Нет рода работы — нет и канала: правка ничего не ломает в обычном отборе."""
    policy = MemoryRetrievalPolicy()
    records = [_record("Столица Франции — Париж.")]
    assert policy._add_lessons_by_work_kind([], records, frozenset()) == []
