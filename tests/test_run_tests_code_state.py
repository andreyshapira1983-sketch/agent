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

from pathlib import Path

from core.code_state import describe_code_state
from tests.conftest import run_git
from tools.run_tests import RunTestsTool

_REPO = Path(__file__).resolve().parents[1]


def test_the_state_names_the_commit_and_branch(git_repo: Path):
    state = describe_code_state(git_repo)

    assert state["commit"], "отпечаток без коммита не отвечает на вопрос «какой код»"
    assert len(state["commit"]) >= 7
    assert state["branch"] == "main"


def test_edits_after_indexing_are_counted(git_repo: Path):
    """Правки поверх зафиксированного кода — признак «проверяли не то»."""
    (git_repo / "b.py").write_text("x = 1\n", encoding="utf-8")

    assert describe_code_state(git_repo)["files_newer_than_index"] == 1


def test_diverging_from_the_shared_branch_is_visible(git_repo: Path):
    """Ровно случай прогона: копия не та, что у всех, и это обязано быть видно."""
    (git_repo / "a.txt").write_text("three\n", encoding="utf-8")
    run_git(git_repo, "add", "-A")
    run_git(git_repo, "commit", "-m", "second")
    run_git(git_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    run_git(git_repo, "reset", "--hard", "HEAD~1")

    state = describe_code_state(git_repo)

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
