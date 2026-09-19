"""Что ход ВЫЯСНИЛ — в долговременную память, а не что он прочитал по пути.

Замер 2026-09-19 (внешний опыт с библиотекой книг, двенадцать вопросов вида
«как называется раздел 2.2 у Judson», «на какой странице эпиграф»): агент
находил ответы, а по памяти, с закрытыми файлами, вспоминал 2 из 12. В
долговременную память шли только предложения источников, случайные для
вопроса («Copyright © 2015 Allen Downey»), а найденный ответ — «раздел 2.2
у Judson называется The Division Algorithm» — не сохранялся никогда: он
заголовок, а не предложение, и существует только как вывод хода.

Правило: вывод записывается, когда эпизод допущен в опыт
(`decide_usage_eligibility`: исход — успех, выполнение подтверждено, есть
проверенные утверждения, ни одного дисквалифицирующего сигнала) и ответ
опирается на источник этого хода. Запись идёт обычной дорогой `remember`:
согласие (тег `fact`), дубли, эхо, заморозка аудита — всё действует.
"""
from __future__ import annotations

import re
from typing import Any

#: Ссылки ответа `[file:…]`, `[topic-only:…]`, `[claim-…]` — адреса, а не знание.
_CITATION = re.compile(r"\s*\[[^\[\]\n]{1,200}\]")
_CONCLUSION = re.compile(
    r"Conclusion:\s*(.*?)(?:\n\s*(?:Facts|Sources|Confidence|Unverified|Safety)\s*:|$)",
    re.DOTALL | re.IGNORECASE,
)
#: Вывод «не нашёл / не знаю» — честный, но знанием о мире не является.
_NOT_FOUND = re.compile(
    r"не\s+удалось|не\s+знаю|не\s+найден|не\s+обнаружен|нет\s+в\s+библиотек|"
    r"could\s+not|not\s+found|unable\s+to|don'?t\s+know",
    re.IGNORECASE,
)
MAX_CONCLUSION_CHARS = 600
_WEB_SOURCE = re.compile(r"(?:web|search|rss|semantic_scholar)[:_]|tool_output:web|https?://", re.IGNORECASE)
#: `admitted-episode`, не «verified»: замер 2026-09-19 (четвёртый прогон
#: библиотеки) — допущенный эпизод записал «эпиграф на странице 326» при верной
#: 327. Допуск подтверждает, что утверждения опираются на прочитанное в этом
#: ходе, а не что вывод верен; метка не обещает больше, чем проверено.
TAGS = ["fact", "conclusion", "admitted-episode"]


def conclusion_of(answer: str) -> str:
    """Раздел Conclusion ответа без ссылок; пусто, если раздела нет."""
    m = _CONCLUSION.search(answer or "")
    if not m:
        return ""
    text = _CITATION.sub("", m.group(1))
    return " ".join(text.split())[:MAX_CONCLUSION_CHARS].strip()


def conclusion_memory(episode: Any) -> str | None:
    """Текст записи «вопрос → вывод → источники», или None, если писать нечего."""
    if not getattr(episode, "usage_eligible", False):
        return None
    sources = [s for s in (getattr(episode, "source_labels", None) or []) if s]
    if not sources:
        return None  # вывод без источника этого хода — пересказ, а не выясненное
    # Два мира — два статуса (оператор, 2026-09-19): локальная библиотека —
    # источник истины, интернет — источник, который ещё надо квалифицировать.
    # Вывод, опирающийся на сеть, остаётся опытом в эпизоде и в долговременную
    # память как факт сам не попадает.
    if any(_WEB_SOURCE.match(s) for s in sources):
        return None
    conclusion = conclusion_of(getattr(episode, "full_answer", "") or "")
    question = " ".join(str(getattr(episode, "question", "") or "").split())
    if not conclusion or not question or _NOT_FOUND.search(conclusion):
        return None
    return (
        f"Вопрос: {question[:300]}\n"
        f"Вывод: {conclusion}\n"
        f"Источники: {', '.join(sources[:5])[:300]}"
    )


def _question_of(content: str) -> str:
    first = (content or "").split("\n", 1)[0]
    return first[len("Вопрос: "):].strip() if first.startswith("Вопрос: ") else ""


def _conclusion_line(content: str) -> str:
    for line in (content or "").splitlines():
        if line.startswith("Вывод: "):
            return line[len("Вывод: "):].strip()
    return ""


def superseded_by(new_content: str, records: list[Any]) -> tuple[list[Any], bool]:
    """Прежние выводы по ТОМУ ЖЕ вопросу и признак «этот вывод уже есть».

    Замер 2026-09-19 (четвёртый прогон библиотеки): в памяти остался неверный
    вывод «страница 326». Исправленный вывод по тому же вопросу почти дословно
    совпадает со старым, и защита от дублей отвергла бы его — ошибка
    закреплялась бы навсегда. Новый вывод, отличный от прежнего, заменяет его;
    старый уходит в архив, а не стирается.
    """
    question = _question_of(new_content)
    fresh = _conclusion_line(new_content)
    same = [r for r in records
            if "conclusion" in (getattr(r, "tags", None) or [])
            and question and _question_of(getattr(r, "content", "")) == question]
    if any(_conclusion_line(r.content) == fresh for r in same):
        return [], True
    return same, False
