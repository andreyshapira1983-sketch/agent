"""Задача над названными файлами — не вопрос о диагностике цитат (правка агента claude_0924).

Агент нашёл и проверил 23.09: «Изучи <файл> и проверь, подтверждается ли
цитата» уводилось в разбор своего проверщика вместо работы над файлом.
"""
from core.doc_routing import is_confidence_evidence_diagnostic_question


def test_study_task_with_named_path_is_not_diagnostic():
    text = "Изучи data/notes/2026-09-24.md и проверь, подтверждается ли цитата из ответа."
    assert is_confidence_evidence_diagnostic_question(text) is False


def test_confidence_evidence_question_without_paths_is_diagnostic():
    text = "почему цитата в твоём ответе не подтвердилась"
    assert is_confidence_evidence_diagnostic_question(text) is True
