"""Прогон тестов обязан говорить, КАКОЙ код он проверял.

Живой прогон 2026-08-03, ход 3. Агент запустил набор, увидел «6625 passed,
1 failed» и подал это как факт: «мой точечный дефект — упал тест
test_model_router_for_task_standard_equals_for_role». Верификатор пометил
утверждение `verified` — цитата ведь совпадала с выводом pytest.

Утверждение было ложным. На main этот тест зелёный; красным он был только в
рабочей копии агента, отставшей от ветки. Улика подтверждала «в этой папке
сейчас что-то красное», а он прочитал её как «в проекте есть баг» — и
проверка это пропустила, потому что сверяет цитату с текстом улики, но не
спрашивает, о каком состоянии кода улика вообще говорит.

Здесь закрепляется минимум, который делает такую подмену видимой: результат
прогона несёт отпечаток проверенного кода — коммит, ветку, чистоту дерева и
отставание от общей ветки.
"""
from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

import pytest

from core.code_state import describe_code_state
from tools.run_tests import RunTestsTool

_REPO = Path(__file__).resolve().parents[1]


def _git(ws: Path, *args: str) -> None:
    # Подавления S603/S607: git с argv из литералов, вход фиксирован.
    subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", "-c", "user.name=T", "-c", "user.email=t@localhost", *args],  # noqa: S607
        cwd=str(ws), check=True, capture_output=True, text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)  # noqa: S607  # nosec B603 B607
    subprocess.run(  # nosec B603 B607
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],  # noqa: S607
        cwd=str(tmp_path), check=True, capture_output=True,
    )
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def test_the_state_names_the_commit_and_branch(repo: Path):
    state = describe_code_state(repo)

    assert state["commit"], "отпечаток без коммита не отвечает на вопрос «какой код»"
    assert len(state["commit"]) >= 7
    assert state["branch"] == "main"


def test_edits_after_indexing_are_counted(repo: Path):
    """Правки поверх зафиксированного кода — признак «проверяли не то»."""
    (repo / "b.py").write_text("x = 1\n", encoding="utf-8")

    assert describe_code_state(repo)["files_newer_than_index"] == 1


def test_diverging_from_the_shared_branch_is_visible(repo: Path):
    """Ровно случай прогона: копия не та, что у всех, и это обязано быть видно."""
    (repo / "a.txt").write_text("three\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "second")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "reset", "--hard", "HEAD~1")

    state = describe_code_state(repo)

    assert state["shared_commit"], "общая ветка известна, но не прочитана"
    assert state["matches_shared"] is False, (
        "расхождение с общей веткой не видно — именно оно объясняет, "
        "почему тест красный здесь и зелёный у всех"
    )


def test_a_directory_without_git_says_so_instead_of_guessing(tmp_path: Path):
    state = describe_code_state(tmp_path)

    assert state["commit"] is None
    assert state["reason"], "нет коммита — обязана быть названа причина"


def test_the_tool_result_carries_the_state():
    """Главное: улика самоописательна — по ней видно, какой код проверялся."""
    result = RunTestsTool(workspace_root=_REPO).run(
        paths=["tests/test_run_tests_code_state.py"], pattern="test_the_state_names",
    )

    assert "code_state" in result, (
        "результат прогона не говорит, какой код проверялся — «тест упал» "
        "нельзя отличить от «упал у меня в устаревшей копии»"
    )
    assert result["code_state"]["workspace"] == str(_REPO)
    assert result["code_state"]["commit"]
