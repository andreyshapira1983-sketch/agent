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
прогона несёт отпечаток проверенного кода — коммит, ветку, совпадение с
общей веткой и число файлов, изменённых после индексации.
"""
from __future__ import annotations

from pathlib import Path

from core.code_state import describe_code_state
from tests.conftest import run_git


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
