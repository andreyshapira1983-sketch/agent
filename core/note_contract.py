"""Договор конспекта: что обязано быть в следе учебной задачи, решает код.

Оператор 2026-09-22 вечером: «ты чинишь симптомы». Так и было: критерий
успеха придумывала та же модель, что делала работу, и каждая заплата
закрывала одну лазейку — книга вместо работы (0630e28), шаблон вместо
конспекта (47ee14e), «зелёный» вместо красного (3edca38). Пока проверку
пишет исполнитель, он найдёт следующую.

Здесь критерий задаёт КОД, одинаково для всех учебных задач, и проверяет
машина: конспект обязан назвать источник (файл рабочей папки или ссылку),
привести дословную цитату и показать проверку с числом. Модель предлагает
только саму задачу.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

NOTES_DIR = "data/notes"
_NOTE_RE = re.compile(r"data/notes/[\w.\-]+\.md")
_URL_RE = re.compile(r"https?://\S{6,}")
_WORKSPACE_PATH_RE = re.compile(r"\b(?:knowledge_library|math_study|core|tools|docs|knowledge)/[\w./\-]+\.\w{1,5}\b")
_QUOTE_RE = re.compile(r"^\s*>\s*\S|«[^»]{15,}»|\"[^\"]{15,}\"", re.MULTILINE)
_NUMBER_RE = re.compile(r"\d")
_CHECK_WORDS = ("проверк", "probe", "расчёт", "расчет", "сверк", "численно", "check")
MIN_CHARS = 400


def criterion(note_rel: str) -> str:
    """Критерий успеха учебной задачи — один и тот же, пишет его код."""
    return (f"Файл {note_rel} создан: источник (путь в рабочей папке или ссылка), "
            "дословная цитата и проверка с числом.")


def contract_gaps(text: str, workspace: Path | str) -> list[str]:
    """Чего не хватает В ЭТОМ ТЕКСТЕ по договору конспекта. Пусто — договор цел.

    Вынесено из `settle_note` 2026-09-23, чтобы ОДИН критерий проверялся в двух
    местах: при судействе цели и ДО записи файла. Замер того же дня: договор
    проверялся только при судействе, и пустышка всё равно ложилась на диск —
    40 конспектов из 127 (31%) содержат признание, что источник не читался,
    цитаты нет и проверка не выполнялась. Цикл при этом помечен `empty`:
    система знала, что работы не было, а файл лежал рядом с настоящими и со
    стороны выглядел как «смотри, я сделал».

    Отдельного списка оборотов («в выводах шагов отсутствует», «дословной
    цитаты нет») здесь НЕТ и быть не должно: список заплат закрывает по одной
    лазейке, а исполнитель находит следующую — тот самый упрёк оператора
    2026-09-22 «ты чинишь симптомы». Договор один и тот же: назван источник,
    приведена дословная цитата, показана проверка с числом. Конспект, который
    признаётся, что источника не читал, проваливает его сам — не потому что
    он так СКАЗАЛ, а потому что цитаты и проверки в нём нет.
    """
    from core.observation_round import unfilled_placeholders

    root = Path(workspace)
    holes = unfilled_placeholders(text)
    named = [p for p in _WORKSPACE_PATH_RE.findall(text) if (root / p).exists()]
    missing: list[str] = []
    if len(text) < MIN_CHARS:
        missing.append(f"конспект короче {MIN_CHARS} знаков")
    if holes:
        missing.append("незаполненные места шаблона: " + "; ".join(h[:60] for h in holes[:2]))
    if not named and not _URL_RE.search(text):
        missing.append("не назван источник: путь существующего файла рабочей папки или ссылка")
    if not _QUOTE_RE.search(text):
        missing.append("нет дословной цитаты (строка с «>» или текст в кавычках)")
    if not (any(w in text.lower() for w in _CHECK_WORDS) and _NUMBER_RE.search(text)):
        missing.append("нет проверки с числом (расчёт, сверка, python_probe)")
    return missing


def settle_note(workspace: Path | str, success_check: str) -> dict[str, Any] | None:
    """Проверить конспект по договору; None — это не задача с конспектом."""
    from core.observation_round import unfilled_placeholders

    match = _NOTE_RE.search(success_check or "")
    if not match:
        return None
    root, rel = Path(workspace), match.group(0)
    path = root / rel
    if not path.is_file():
        return {"verdict": "missing", "reason": f"конспекта {rel} нет"}
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = contract_gaps(text, root)
    if missing:
        return {"verdict": "missing", "reason": f"{rel}: " + "; ".join(missing)}
    return {"verdict": "verified", "reason": f"{rel}: источник, цитата и проверка на месте"}
