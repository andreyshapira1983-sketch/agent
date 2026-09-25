"""Знание о себе и предметная задача: что из памяти НЕ подмешивать при чтении.

Вынесено из core/learned_conclusion.py: тот модуль решает, какой вывод хода
ЗАПИСАТЬ в долговременную память, а здесь — сторона ЧТЕНИЯ: запись о
собственном устройстве агента (его файлы, без книги и сети) не подмешивается
в задачу о предмете (книга, библиотека, сеть). Потребитель —
core/loop_memory_read.py.
"""
from __future__ import annotations

import re

#: Звено 5 («в следующем запуске вспомнил») — вспоминать ПО ТЕМЕ. Замер
#: 2026-09-19: в каждый заход подмешивались 3 записи по совпадению слов, и чаще
#: всех — разборы собственного кода и журналов (детекторы — 51 раз,
#: step_sanitizer — 51, best_next_action — 41) в задачи по физике и математике.
#: Знание о себе — запись, чьи источники только его файлы, без книги и сети.
_SELF_PATH = re.compile(
    r"(?<![\w.])(?:file:)?(?:core|data|logs|tests|knowledge|scripts|docs|config)/[\w./-]+|\bagent_tick\.py\b")
_SUBJECT_PATH = re.compile(r"math_study/|knowledge_library/|https?://")


def is_self_knowledge(content: str) -> bool:
    """Запись о собственном устройстве агента, а не о предмете."""
    return bool(_SELF_PATH.search(content or "")) and not _SUBJECT_PATH.search(content or "")


def asks_about_self(question: str) -> bool:
    """Вопрос называет собственные файлы агента — тогда знание о себе к месту."""
    return bool(_SELF_PATH.search(question or ""))


#: Предметная задача: книга, библиотека, сеть. Только ей знание о себе мешает;
#: нейтральный вопрос («разбери ответ про политику памяти проекта») — о себе
#: без пути, и его не трогаем (свидетель: tests/test_turn_context_read_c05.py).
_SUBJECT_TASK = re.compile(
    r"math_study/|knowledge_library/|https?://|\bкниг[аеиуо]\w*|\bинтернет\w*|\binternet\b|\bweb\b",
    re.IGNORECASE)


def self_knowledge_off_topic(question: str) -> bool:
    """Задача о предмете и не о собственных файлах — знание о себе не подмешивать."""
    return bool(_SUBJECT_TASK.search(question or "")) and not asks_about_self(question)
