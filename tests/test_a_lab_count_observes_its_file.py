"""Файл, который лаборатория посчитала или поиск прочёл, — наблюдён.

Замер 2026-09-19 (опыт с библиотекой книг): «сколько раз слово entropy
встречается в Tong_StatisticalPhysics.txt» — лаборатория получила файл и
насчитала верные 131, но долг наблюдения за названным файлом числился
`silently_missing`: метка шага — начало кода, а не путь. Все три вопроса на
подсчёт теряли допуск в опыт, и выводы по ним не запоминались.
"""
from __future__ import annotations

from core.completion_obligation import evaluate_completion_obligations

_Q = "Сколько раз слово «entropy» встречается в файле Tong_StatisticalPhysics.txt?"
_FILE = "knowledge_library/physics/txt/Tong_StatisticalPhysics.txt"


def _status(artifacts: dict) -> str:
    result = evaluate_completion_obligations(question=_Q, answer="131 раз", artifacts=artifacts)
    return next(o.status for o in result.obligations if o.kind == "workspace_observation")


def test_a_file_the_lab_was_given_is_observed():
    assert _status({"python_probe:import re": {
        "tool": "python_probe", "output": {"inputs": [_FILE], "stdout": "131\n", "exit_code": 0}}}) == "satisfied"


def test_a_file_the_search_found_lines_in_is_observed():
    assert _status({"find_in_files:entropy": {
        "tool": "find_in_files",
        "output": f"125 matching lines (131 occurrences) in 1 of 1 text files under .\n{_FILE}:12: entropy"}}) == "satisfied"


def test_a_name_only_in_the_search_request_is_not_an_observation():
    """ПРЕДОХРАНИТЕЛЬ: отрицательный ответ поиска повторяет запрос, а не файл."""
    assert _status({"find_in_files:Tong_StatisticalPhysics.txt": {
        "tool": "find_in_files",
        "output": "no matches for 'Tong_StatisticalPhysics.txt' in 90 text files under ."}}) == "silently_missing"
