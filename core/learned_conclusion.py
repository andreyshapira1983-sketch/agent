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


#: Звенья 3–4 цепочки «нашёл в сети → проверил → решил сохранить → записал с
#: родословной» (оператор, 2026-09-19). Правило «сеть в память не пишется»
#: стояло, пока квалификации не было: за сутки 7 задач с интернетом, 0 записей.
#: Квалификация — не домен и не вкус модели, а проверка ЭТОГО хода: у одного
#: утверждения с дословной цитатой вердикт проверяющего «verified», и улика
#: этого утверждения — ОТКРЫТАЯ страница (`web_page:<url>`). Берётся из отчёта
#: проверки, а не из текста ответа: метка `[verified:…]` из ответа человеку
#: намеренно снимается (`core/answer_format._strip_verification_markers`), и
#: первая версия этого правила искала то, чего в тексте не бывает — 146
#: эпизодов, 0 записей (замер 2026-09-20). Поисковая выдача без открытой
#: страницы не квалифицирует ничего.
WEB_TAGS = ["fact", "conclusion", "web-knowledge"]
_QUOTE = re.compile(r"«([^«»\n]{12,400})»|“([^“”\n]{12,400})”|\"([^\"\n]{12,400})\"")
_OWN_WRAPPER = "\n\nThis is your own chosen goal."


def _verified_web_quote(verification: Any, chain: Any) -> tuple[str, str]:
    """(цитата, адрес) из подтверждённого утверждения о веб-странице, или («», «»)."""
    pages = {ev.id: ev.source_id[len("web_page:"):]
             for ev in (getattr(chain, "evidences", None) or [])
             if str(getattr(ev, "source_id", "")).startswith("web_page:")}
    for chunk in (getattr(verification, "chunks", None) or []):
        if chunk.verdict != "verified":
            continue
        url = next((pages[i] for i in chunk.matched_evidence_ids if i in pages), "")
        quote = _QUOTE.search(chunk.text or "")
        if url and quote:
            return next(g for g in quote.groups() if g), url
    return "", ""


def web_knowledge_memory(episode: Any, verification: Any = None, chain: Any = None) -> str | None:
    """Запись «вопрос → вывод → цитата → адрес и дата», или None, если сеть не квалифицирована."""
    if not getattr(episode, "usage_eligible", False):
        return None
    labels = [str(s) for s in (getattr(episode, "source_labels", None) or [])]
    if not any(s.startswith("web_fetch:http") for s in labels):
        return None  # страницу не открывали — пересказ выдачи, а не источник
    quote, url = _verified_web_quote(verification, chain)
    if not quote:
        return None  # ни одна цитата не подтверждена по странице — сохранять нечего
    answer = getattr(episode, "full_answer", "") or ""
    conclusion = conclusion_of(answer)
    question = " ".join(str(getattr(episode, "question", "") or "").split(_OWN_WRAPPER)[0].split())
    if not url or not conclusion or not question or _NOT_FOUND.search(conclusion):
        return None
    read_on = str(getattr(episode, "created_at", "") or "")[:10]
    return (
        f"Вопрос: {question[:300]}\n"
        f"Вывод: {conclusion}\n"
        f"Цитата: «{quote}»\n"
        f"Источник: {url} (прочитан {read_on})"
    )


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
