"""Recall by topic: notes about the agent's own code stay out of subject tasks.

2026-09-19: three records were injected per pass by word overlap, and the most
injected were the agent's analyses of its own code and logs (detectors 51
times, step_sanitizer 51, best_next_action 41) — into physics and math tasks.
A record whose sources are only the agent's own files is self-knowledge; it is
offered only when the question names the agent's own files.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.learned_conclusion import asks_about_self, is_self_knowledge, self_knowledge_off_topic

_SELF = ("Вопрос: Прочитай core/theorem_sorter.py и найди, где сортируются теоремы\n"
         "Вывод: теоремы сортирует функция sort_theorems в core/theorem_sorter.py\n"
         "Источники: file:core/theorem_sorter.py")
_SUBJECT = ("Вопрос: В книге knowledge_library/physics/txt/Tong.txt найди теорему Нётер\n"
            "Вывод: теорема Нётер: каждой симметрии соответствует сохраняющаяся величина\n"
            "Источники: file:knowledge_library/physics/txt/Tong.txt")


def test_the_rule_on_the_records_of_the_day() -> None:
    assert is_self_knowledge(_SELF) and not is_self_knowledge(_SUBJECT)
    assert not asks_about_self("Найди в книге теорему Нётер и проверь её")
    assert asks_about_self("Прочитай data/charter_decisions.jsonl и найди записи")
    assert self_knowledge_off_topic("Найди теорему Нётер в книге и проверь её")
    assert self_knowledge_off_topic("Найди в интернете первоисточник о теореме Гёделя")
    assert not self_knowledge_off_topic("разбери этот ответ про политику памяти проекта"), "о себе без пути"
    assert not self_knowledge_off_topic("Прочитай core/x.py и сверь с книгой"), "назван свой файл"


@pytest.fixture
def agent(tmp_path: Path):
    writer = build_agent(tmp_path, with_memory=True, with_persistent=True)
    for text in (_SELF, _SUBJECT):
        decision, _ = writer.remember(content=text, tags=["fact", "conclusion"], source="user-explicit",
                                      record_type="semantic", owner="user")
        assert decision.decision == "save", decision.reasons
    return build_agent(tmp_path, with_memory=True, with_persistent=True)


def test_a_subject_task_gets_the_subject_not_the_self(agent) -> None:
    injected = agent._retrieve_persistent("Найди теорему Нётер в книге и проверь, где сортируются теоремы")
    assert "Нётер" in injected
    assert "sort_theorems" not in injected


def test_a_question_about_own_files_still_gets_self_knowledge(agent) -> None:
    injected = agent._retrieve_persistent("Прочитай core/theorem_sorter.py: где сортируются теоремы?")
    assert "sort_theorems" in injected
