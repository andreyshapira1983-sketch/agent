"""Note contract: code, not the model, decides what a study-task trace must contain (source, verbatim quote, check with a number)."""
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
    """Success criterion of a study task: the same for all tasks, written by code."""
    return (f"Файл {note_rel} создан: источник (путь в рабочей папке или ссылка), "
            "дословная цитата и проверка с числом.")


def contract_gaps(text: str, workspace: Path | str) -> list[str]:
    """What is missing IN THIS TEXT per the note contract; empty means the contract holds.

    One criterion checked in two places: when the goal is judged and BEFORE the file is written. There is deliberately no list of
    phrasings: a note admitting it did not read the source fails because it has no quote or check.
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
    """Check a note against the contract; None means this is not a task with a note."""

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
