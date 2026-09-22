"""Критерий успеха пишет код, а не исполнитель (core/note_contract.py).

Слово оператора 2026-09-22 вечером: «ты чинишь симптомы». Так и было: пока
проверку придумывает та же модель, что делает работу, она находит следующую
лазейку — книга вместо работы, шаблон вместо конспекта, «зелёный» вместо
красного. Договор конспекта один для всех учебных задач и проверяется машиной.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.drive_goal import _with_notes
from core.note_contract import criterion, settle_note

_NOW = datetime(2026, 9, 22, 20, tzinfo=timezone.utc)
_GOOD = """# Теорема Евклида

Источник: math_study/library/txt/Stein_ElementaryNumberTheory.txt, строки 521-522.

> Theorem 1.2.1 (Euclid). There are infinitely many primes.

Ключевой шаг: N = p1*p2*...*pn + 1 не делится ни на одно из p_i.

## Проверка python_probe

Для простых до 47: N = 614889782588491411, наименьший делитель 953 — его нет в списке.
Значит, найдено новое простое, что и утверждает теорема. Расчёт сошёлся.
"""


def _note(tmp_path: Path, text: str) -> str:
    book = tmp_path / "math_study" / "library" / "txt" / "Stein_ElementaryNumberTheory.txt"
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_text("Theorem 1.2.1 (Euclid).\n", encoding="utf-8")
    rel = "data/notes/20260922T200000_competence_math.md"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


def test_the_model_does_not_write_the_criterion() -> None:
    goal, check = _with_notes("Прочитай раздел.", "я сам решу, что считать успехом", "competence_math", _NOW)
    assert check == criterion("data/notes/20260922T200000_competence_math.md")
    assert "я сам решу" not in check
    assert "data/notes/20260922T200000_competence_math.md" in goal


def test_a_note_with_source_quote_and_a_checked_number_passes(tmp_path: Path) -> None:
    rel = _note(tmp_path, _GOOD)
    assert settle_note(tmp_path, criterion(rel))["verdict"] == "verified"


def test_a_note_without_a_quote_or_without_a_check_does_not_pass(tmp_path: Path) -> None:
    rel = _note(tmp_path, _GOOD.replace("> Theorem 1.2.1 (Euclid). There are infinitely many primes.", "теорема про простые"))
    assert "цитаты" in settle_note(tmp_path, criterion(rel))["reason"]

    rel = _note(tmp_path, _GOOD.split("## Проверка", maxsplit=1)[0] + "\n" * 30 + "ещё немного текста про теорему " * 12)
    assert "проверки с числом" in settle_note(tmp_path, criterion(rel))["reason"]


def test_a_template_or_a_stub_source_does_not_pass(tmp_path: Path) -> None:
    rel = _note(tmp_path, _GOOD.replace("Расчёт сошёлся.", "(заполняется по прочитанному разделу книги)"))
    assert "шаблона" in settle_note(tmp_path, criterion(rel))["reason"]

    rel = _note(tmp_path, _GOOD.replace("math_study/library/txt/Stein_ElementaryNumberTheory.txt", "книга по теории чисел"))
    assert "источник" in settle_note(tmp_path, criterion(rel))["reason"]


def test_a_goal_without_a_note_is_left_to_the_ordinary_judge(tmp_path: Path) -> None:
    assert settle_note(tmp_path, "в книге найдена теорема") is None
