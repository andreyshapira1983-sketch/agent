"""Модель предлагает слияние памяти, ворота решают (core/memory_consolidation.gate).

«Useful Memories Become Faulty» (arXiv 2605.12978): непрерывно переписываемая
моделью память деградирует; сырое хранить, слияние — через ворота. Замер
2026-09-25 на 8 реальных слияниях сервера: ворота пропускают 4 (тот же вопрос
или тот же файл) и останавливают 4 — среди них склейку двух РАЗНЫХ сообщений
кода («goal first: …» и «pursue_goal: …»); остановленное пишется рядом (ADD).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.memory_consolidation import Consolidation, gate, merged_content, same_subject

_BOOK = "knowledge_library/cs/txt/Erickson_Algorithms.txt"


@pytest.mark.parametrize(("old", "new", "same"), [
    (f"Прочитать в {_BOOK} раздел про инвариант Дейкстры", f"Выписать псевдокод Дейкстры из {_BOOK}", True),
    ("В math_study/drafts/M11 найти в diff-файле утверждение с формулой",
     "В рабочей папке math_study/drafts/M11 найди в одном из diff-файлов утверждение", True),
    ("Найти код, который формирует сообщение 'goal first: the goal names ... as the work'",
     "Найти код, который формирует сигнал 'pursue_goal: goal ... has no admissible action'", False),
    ("В книге knowledge_library/physics/txt/Tong_ClassicalDynamics.txt теорема Нётер",
     "В книге knowledge_library/cs/txt/Mogensen_BasicsOfCompilerDesign.txt лексический анализ", False),
])
def test_one_subject_is_the_same_question_or_the_same_named_file(old: str, new: str, same: bool) -> None:
    assert same_subject(old, new) is same


def _record(rid: str, question: str) -> SimpleNamespace:
    return SimpleNamespace(id=rid, content=f"Вопрос: {question}\nВывод: ...")


def test_a_merge_across_subjects_becomes_an_add_and_says_why() -> None:
    old = _record("mem_1", "В книге knowledge_library/physics/txt/Tong_ClassicalDynamics.txt теорема Нётер")
    decided = gate(Consolidation("UPDATE", target_id="mem_1", merged="..."),
                   "В книге knowledge_library/cs/txt/Mogensen_BasicsOfCompilerDesign.txt лексика", [old])
    assert decided.operation == "ADD" and "different subject" in decided.reason


def test_a_merge_of_the_same_subject_passes_and_a_foreign_target_does_not() -> None:
    old = _record("mem_1", f"Прочитать в {_BOOK} раздел про инвариант Дейкстры")
    same = Consolidation("UPDATE", target_id="mem_1", merged="m")
    assert gate(same, f"Выписать псевдокод Дейкстры из {_BOOK}", [old]) is same
    foreign = gate(Consolidation("DELETE", target_id="mem_9"), "q", [old])
    assert foreign.operation == "ADD" and "not among the shown candidates" in foreign.reason
    noop = Consolidation("NOOP", target_id="mem_1")
    assert gate(noop, "anything", [old]) is noop


def test_a_merged_conclusion_does_not_nest_question_inside_conclusion() -> None:
    nested = "Вопрос: Что ты делаешь? Вывод: Вопрос: Что делает кампания? Вывод: Кампания стоит, 3 цикла."
    out = merged_content("Вопрос: Что делает кампания?\nВывод: x", "Вопрос: Что ты делаешь?\nВывод: y", nested)
    assert out == "Вопрос: Что ты делаешь?\nВывод: Кампания стоит, 3 цикла."


def test_the_gate_stands_on_the_write_path() -> None:
    """Ворота — на пути записи вывода, а не отдельная функция."""
    import json

    from core.loop_memory_write import AgentLoopMemoryWrite

    class _Model:
        def complete(self, *_a, **_kw) -> str:
            return json.dumps({"operation": "UPDATE", "target": "mem_1", "merged": "merged text", "title": None})

    old = SimpleNamespace(id="mem_1", tags=["conclusion"],
                          content="Вопрос: В книге knowledge_library/physics/txt/Tong_ClassicalDynamics.txt Нётер\n"
                                  "Вывод: симметрия даёт сохраняющуюся величину")
    host = SimpleNamespace(llm=_Model())
    episode = SimpleNamespace(question="В книге knowledge_library/cs/txt/Mogensen.txt лексический анализ Нётер")
    content = ("Вопрос: В книге knowledge_library/cs/txt/Mogensen.txt лексический анализ\n"
               "Вывод: лексический анализ делит текст на токены; Нётер тут ни при чём")
    decided = AgentLoopMemoryWrite._consolidate_conclusion(host, content, episode, [old])
    assert decided.operation == "ADD" and "different subject" in decided.reason
