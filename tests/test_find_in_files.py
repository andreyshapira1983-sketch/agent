"""Поиск по рабочей папке: отрицательный ответ говорит, сколько просмотрено.

Замер 2026-09-19 (опыт с библиотекой книг): искать было нечем, кроме findstr,
и провалы были одного вида — «такой книги нет», хотя она была: findstr падал
на синтаксисе, пустой вывод читался как отсутствие. См. tools/find_in_files.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.evidence import evidence_from_tool_result
from core.step_sanitizer import sanitize_step
from tools.find_in_files import FindInFilesTool


def _library(root: Path) -> Path:
    books = root / "math_study" / "library" / "txt"
    books.mkdir(parents=True)
    (books / "Lebl_BasicAnalysis_I.txt").write_text(
        "===== PAGE 28 =====\nintro\n===== PAGE 29 =====\n1.2 The set of real numbers\n",
        encoding="utf-8")
    (root / "knowledge_library").mkdir()
    (root / "knowledge_library" / "INDEX.md").write_text("physics and cs only\n", encoding="utf-8")
    (root / ".env").write_text("KEY=The set of real numbers", encoding="utf-8")
    return root


def test_a_book_is_found_by_name_anywhere_in_the_workspace(tmp_path: Path):
    out = FindInFilesTool(_library(tmp_path)).run(name="*lebl*")
    assert out.splitlines() == ["1 files under . (name=*lebl*)",
                                "math_study/library/txt/Lebl_BasicAnalysis_I.txt"]


def test_a_phrase_is_found_with_its_line_number(tmp_path: Path):
    out = FindInFilesTool(_library(tmp_path)).run(query="the set of REAL numbers")
    assert "math_study/library/txt/Lebl_BasicAnalysis_I.txt:4: 1.2 The set of real numbers" in out
    assert ".env" not in out, "файл ключей не просматривается никогда"


def test_a_negative_answer_says_how_much_was_searched(tmp_path: Path):
    out = FindInFilesTool(_library(tmp_path)).run(query="Lao-Tzu", path="knowledge_library")
    assert out == "no matches for 'Lao-Tzu' in 1 text files under knowledge_library (name=*)"


def test_regex_and_the_workspace_boundary(tmp_path: Path):
    tool = FindInFilesTool(_library(tmp_path))
    assert "2 matching lines (2 occurrences)" in tool.run(query=r"PAGE \d+", regex=True)
    (tmp_path / "twice.txt").write_text("entropy and entropy\n", encoding="utf-8")
    assert tool.run(query="entropy", name="twice.txt").startswith("1 matching lines (2 occurrences)")
    with pytest.raises(PermissionError):
        tool.run(query="x", path="../")


def test_the_planner_step_and_the_evidence_are_wired():
    warnings: list[str] = []
    step = sanitize_step("find_in_files", {"name": "*Lebl*"}, None, 0, warnings)
    assert step is not None and step["arguments"]["name"] == "*Lebl*", warnings
    assert sanitize_step("find_in_files", {"path": "../x"}, None, 1, warnings) is None
    ev = evidence_from_tool_result(tool_name="find_in_files", arguments={"path": "."},
                                   output="1 files under .\nmath_study/Lebl.txt")
    assert ev is not None and ev.source_id == "tool_output:find_in_files"
    assert "Lebl.txt" in ev.excerpt


def test_a_file_path_searches_inside_that_file(tmp_path: Path):
    """Замер 2026-09-19: поиск с путём к книге отвечал «Directory not found»."""
    tool = FindInFilesTool(_library(tmp_path))
    out = tool.run(query="real numbers", path="math_study/library/txt/Lebl_BasicAnalysis_I.txt")
    assert out.startswith("1 matching lines (1 occurrences) in 1 of 1 text files")
    with pytest.raises(PermissionError):
        tool.run(query="KEY", path=".env")


def test_the_agents_own_journal_is_not_searched_as_the_workspace(tmp_path: Path):
    """Веб-экзамен 2026-09-19, второй прогон, N01: поиск «release» по «.»
    находил только `logs/trace_….jsonl` — журнал этого хода, где записан сам
    запрос, — и агент отвечал «не найдено» над эхом своего вопроса."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "RELEASE.md").write_text("Текущий выпуск: 5.7.8\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "trace_x.jsonl").write_text('{"query": "release"}\n', encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "persistent_memory.jsonl").write_text('{"content": "release"}\n', encoding="utf-8")
    tool = FindInFilesTool(tmp_path)
    out = tool.run(query="release")
    assert "logs/" not in out and "data/" not in out and "no matches" in out
    assert "RELEASE.md" in tool.run(name="*release*")
    assert "trace_x.jsonl" in tool.run(query="release", path="logs"), "asked for by path, it is searched"
