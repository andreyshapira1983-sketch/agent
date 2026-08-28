"""Пометка, оставленная человеком в коде, больше не превращается в работу.

Слово оператора 2026-08-28: «убрать TODO/FIXME — это старая модель, вообще
стереть». Источник `code_todo` (сканер маркеров TODO/FIXME/XXX по дереву)
был последним каналом модели «человек назначает — агент исполняет» внутри
автономного бэклога: инженерная пометка читалась как задача. Орган стёрт
целиком — сканер, веса, кадр подсказки Stage A; законные якоря — только
собственные измерения агента (аудит архитектуры, датчик размера).

Это стирание ОТМЕНЯЕТ пункт решения 2026-08-19 («code_todo оставить —
настоящий TODO в коде всё же заземлённый дефект»): новое слово новее и
мотивировано сменой модели. История: MIR-183.

Красный свидетель: до стирания первый тест краснел — посаженный TODO
честно становился кандидатом бэклога (это проверял умерший вместе с органом
tests/test_backlog_code_todo.py).
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from core.backlog_selector import _SOURCE_BASE_SCORE, load_backlog


def test_a_planted_todo_yields_no_backlog_candidate(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "example.py").write_text(
        dedent(
            """
            # TODO: refactor this function for clarity
            def some_function():
                # FIXME: handle None
                pass
            """
        ),
        encoding="utf-8",
    )

    candidates = load_backlog(tmp_path)

    marked = [
        c for c in candidates
        if c.signal_source == "code_todo" or "TODO" in c.problem_quote
        or "FIXME" in c.problem_quote
    ]
    assert marked == [], (
        "человеческая пометка снова стала работой: " + repr(marked)
    )


def test_the_erased_source_has_no_rank() -> None:
    """Вес в таблице рангов — это право участвовать; у стёртого его нет."""
    assert "code_todo" not in _SOURCE_BASE_SCORE
