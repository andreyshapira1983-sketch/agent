"""Пунктуация приклеивалась к слову и убивала совпадение по теме.

Background: docs/CODE_NOTES.md, "A comma is not a letter".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.smart_memory import ProceduralMemoryStore, ProcedureRecord, _tokens

#: Замерено живьём 2026-08-15 на хранилище из 31 записи.
_MEASURED = "Найди, где в core рождается сигнал reasoning_action_mismatch, и покажи строку."


def test_the_measured_query_no_longer_hides_its_own_subject():
    """Запятая после имени сигнала делала `reasoning_action_mismatch,`
    токеном, которого нет ни в одной записи. Совпадение решали «где» и «это»,
    а тема в счёт не шла вовсе.
    """
    tokens = _tokens(_MEASURED)

    assert {"reasoning", "action", "mismatch", "core"} <= tokens
    assert not any(t.endswith((",", ".", ":", ";")) for t in tokens)


@pytest.mark.parametrize("text,expected", [
    ("перечисли ВСЕ файлы каталога core, где встречается имя", "core"),
    ('он ответил "citation_fabricated" — и замолчал', "citation_fabricated"),
    ("сигнал (evidence_budget_trim) сработал; проверь", "evidence_budget_trim"),
    ("в файле core/loop.py: строка 42!", "loop"),
    ("«эпизод» — что это?", "эпизод"),
])
def test_unseen_punctuation_no_longer_glues(text: str, expected: str):
    """Правило проверяется формами, под которые его не подгоняли: кавычки,
    тире, скобки, точка с запятой, восклицательный знак, ёлочки.
    """
    assert set(expected.split("_")) <= _tokens(text)


def test_short_and_numeric_tokens_keep_their_old_standing():
    """Порог длины и цифровая оговорка (CORE-10) не менялись — менялся только
    разделитель. Тест держит их на месте, чтобы правка не уехала дальше.
    """
    assert "v2" in _tokens("версия v2")
    assert "17" in _tokens("строка 17")
    assert "не" not in _tokens("не так")


def _proc(name: str, tags: tuple[str, ...]) -> ProcedureRecord:
    return ProcedureRecord(
        name=name,
        workflow_key="tools:list_dir->file_read",
        trigger_tags=tags,
        steps=(f"Situation: {name}", "Run tool: file_read"),
        status="candidate",
    )


def test_the_topical_record_wins_over_the_one_sharing_only_function_words(tmp_path: Path):
    """Измеренный провал целиком: запись про ТЕ ЖЕ сигналы проигрывала записи,
    делившей с запросом только служебные слова, потому что имена сигналов в
    запросе стояли с запятыми.

    Два имени, а не одно, намеренно. Запятая съедает ровно один токен на
    имя, и при одном имени счёт выходил в ничью — тогда исход решал порядок
    записей, а не тема, и тест зеленел бы и на несломанном, и на сломанном
    коде. С двумя разрыв строгий в обе стороны: до правки побеждали служебные
    слова, после — тема.
    """
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([
        _proc("причина сигналов", ("reasoning_action_mismatch", "citation_fabricated")),
        _proc("прочее", ("где", "покажи", "строку", "рождается")),
    ])

    offered = store.search_with_report(
        "Найди, где в core рождается сигнал reasoning_action_mismatch, "
        "и citation_fabricated, покажи строку."
    ).procedures

    assert offered[0].name == "причина сигналов", "тему снова обошли служебные слова"
