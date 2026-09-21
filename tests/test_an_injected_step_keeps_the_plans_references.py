"""Вставленный в план шаг не сдвигает ссылки {{step:N.output}} на чужие шаги.

2026-09-21, живой разговор: агент спланировал «прочитать final (1), прочитать
дополнение (2), записать final = {{step:1.output}} … {{step:2.output}}».
Маршрутизатор документов после планировщика вставил в начало чтение доктрины
самопочинки (сработали «исправ» + «резервн» в «исправь … резервная копия»),
шаги сдвинулись, ссылки остались — и в предложение агента записалась доктрина.
Резервная копия спасла файл.
"""
from __future__ import annotations

from core.doc_routing import (
    _ensure_self_repair_doctrine_docs_first,
    _is_self_repair_doctrine_question,
)
from core.step_references import renumber_step_references


def test_references_follow_their_steps_after_an_injection() -> None:
    plan = [
        {"tool": "file_read", "arguments": {"path": "proposals/final.md"}},
        {"tool": "file_read", "arguments": {"path": "proposals/addendum.md"}},
        {"tool": "file_write", "arguments": {"path": "proposals/final.md",
                                             "content": "{{step:1.output}}\n---\n{{step:2.output}}"}},
    ]
    out = _ensure_self_repair_doctrine_docs_first(plan, [])
    assert out[0]["arguments"]["path"].endswith("SELF_REPAIR_DOCTRINE.md")
    write = next(s for s in out if s["tool"] == "file_write")
    assert write["arguments"]["content"] == "{{step:2.output}}\n---\n{{step:3.output}}"


def test_a_dropped_step_leaves_its_reference_to_fail_loudly() -> None:
    a, b = {"tool": "x", "arguments": {}}, {"tool": "y", "arguments": {"v": "{{step:1.output}}"}}
    out = renumber_step_references([a, b], [b])
    assert out[0]["arguments"]["v"] == "{{step:1.output}}"


def test_a_task_on_named_files_is_not_a_repair_doctrine_question() -> None:
    assert not _is_self_repair_doctrine_question(
        "Исправь proposals/2026-09-21_final.md на месте: инструмент сделает резервную копию.")
    assert _is_self_repair_doctrine_question(
        "Как правильно чинить баг: какой протокол, как сделать резервную копию данных?")
