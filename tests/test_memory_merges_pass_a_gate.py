"""Модель предлагает слияние памяти, ворота проверяют, что оно ничего не выронило.

«Useful Memories Become Faulty» (arXiv 2605.12978): непрерывно переписываемая
моделью память деградирует; сырое хранить, слияние — через ворота. Мерка
ворот — та, которой модуль принят 23.09: все числа обеих записей (разделы,
страницы, строки, формулы) в слитом тексте. Первая редакция сравнивала слова
вопросов и остановила каноническое слияние о Нётер у Тонга (полный прогон
2026-09-25, tests/test_a_known_fact_is_not_stored_twice.py).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.memory_consolidation import Consolidation, gate, merged_content

_OLD = "Вопрос: Найди в книге Тонга формулировку теоремы Нётер\nВывод: Раздел 2.4.1, страница 24: симметрия даёт сохранение."
_NEW = "Вопрос: Какой пример теоремы Нётер приводит Тонг (с. 25)\nВывод: Сдвиг даёт сохранение импульса, уравнение (2.31)."


def _old() -> SimpleNamespace:
    return SimpleNamespace(id="mem_1", tags=["conclusion"], content=_OLD)


def test_a_merge_that_keeps_every_number_of_both_passes() -> None:
    merged = "Раздел 2.4.1, страница 24: симметрия даёт сохранение; пример — сдвиг и импульс, уравнение (2.31)."
    decision = Consolidation("UPDATE", target_id="mem_1", merged=merged)
    assert gate(decision, _NEW, [_old()]) is decision


def test_a_merge_that_drops_a_number_keeps_both() -> None:
    decided = gate(Consolidation("UPDATE", target_id="mem_1", merged="Симметрия даёт сохранение; пример — (2.31)."),
                   _NEW, [_old()])
    assert decided.operation == "ADD" and "2.4.1" in decided.reason and "24" in decided.reason


def test_the_questions_own_numbers_are_not_a_loss() -> None:
    """Вопрос слитой записи по замыслу берётся у прежней: «с. 25» из нового вопроса не теряется."""
    merged = "Раздел 2.4.1, страница 24: симметрия даёт сохранение; сдвиг — импульс, уравнение (2.31)."
    assert gate(Consolidation("UPDATE", target_id="mem_1", merged=merged), _NEW, [_old()]).operation == "UPDATE"


def test_a_target_outside_the_shown_candidates_is_an_add() -> None:
    decided = gate(Consolidation("DELETE", target_id="mem_9"), _NEW, [_old()])
    assert decided.operation == "ADD" and "not among the shown candidates" in decided.reason
    noop = Consolidation("NOOP", target_id="mem_1")
    assert gate(noop, _NEW, [_old()]) is noop


def test_a_merged_conclusion_does_not_nest_question_inside_conclusion() -> None:
    nested = "Вопрос: Что ты делаешь? Вывод: Вопрос: Что делает кампания? Вывод: Кампания стоит, 3 цикла."
    out = merged_content("Вопрос: Что делает кампания?\nВывод: x", "Вопрос: Что ты делаешь?\nВывод: y", nested)
    assert out == "Вопрос: Что ты делаешь?\nВывод: Кампания стоит, 3 цикла."


def test_the_gate_stands_on_the_write_path() -> None:
    """Ворота — на пути записи вывода, а не отдельная функция."""
    from core.loop_memory_write import AgentLoopMemoryWrite

    class _Model:
        def complete(self, *_a, **_kw) -> str:
            return json.dumps({"operation": "UPDATE", "target": "mem_1", "merged": "Симметрия даёт сохранение.",
                               "title": None})

    decided = AgentLoopMemoryWrite._consolidate_conclusion(
        SimpleNamespace(llm=_Model()), _NEW, SimpleNamespace(question="Какой пример теоремы Нётер приводит Тонг"),
        [_old()])
    assert decided.operation == "ADD" and "would drop" in decided.reason
