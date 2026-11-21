"""Самопочинка без человека в петле (core/patch_route.py).

Слово оператора 2026-09-22: автомат зовёт человека только ради денег или
настоящего решения. За четыре дня до кода дошли две его правки; цели
самоулучшения кончались прозой. Теперь дефект реестра → правка → patch_check
(полный набор) → штатный путь применения → основная ветка.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.patch_route import defect_goal, settle_patch
from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "core").mkdir(parents=True)
    (root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (root / "core" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (root / ".gitignore").write_text("data/\nproposals/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return root


def test_the_goal_names_an_open_defect_and_its_patch_file(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH).upsert_failure(
        "core.mod.f returns 1 instead of 2", "2026-09-22T00:00:00+00:00")
    goal = defect_goal(root)
    assert goal is not None and "proposals/selffix/" in goal.success_check
    assert defect_goal(root) is None, "тот же дефект не берётся снова раньше срока"


def test_a_red_patch_does_not_land(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    patch = root / "proposals" / "selffix" / "d" / "edits.txt"
    patch.parent.mkdir(parents=True)
    patch.write_text("FILE: tests/test_red.py\n<<<<<<< SEARCH\n=======\ndef test_red():\n    assert False\n"
                     ">>>>>>> REPLACE\n", encoding="utf-8")
    verdict = settle_patch(None, root, "Файл proposals/selffix/d/edits.txt создан")
    assert verdict["verdict"] == "missing"
    assert not (root / "tests" / "test_red.py").exists()


def test_a_green_patch_lands_in_the_main_branch_and_closes_the_defect(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    registry = SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH)
    registry.upsert_failure("core.mod.f returns 1 instead of 2", "2026-09-22T00:00:00+00:00")
    goal = defect_goal(root)
    rel = goal.success_check.split("Файл ", 1)[1].split(" ", 1)[0]
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text(
        "FILE: core/mod.py\n<<<<<<< LINES 2-2\n    return 2\n>>>>>>> REPLACE\n"
        "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n=======\nfrom core.mod import f\n\n\n"
        "def test_f():\n    assert f() == 2\n>>>>>>> REPLACE\n", encoding="utf-8")

    verdict = settle_patch(None, root, goal.success_check)

    assert verdict["verdict"] == "verified", verdict
    assert "return 2" in (root / "core" / "mod.py").read_text(encoding="utf-8")
    assert "self-apply" in _git(root, "log", "-1", "--format=%s").lower() or _git(root, "log", "--oneline").count("\n") >= 2
    assert all(i.status == "resolved" for i in registry.list())


def test_an_environment_defect_is_left_to_the_operator(tmp_path: Path) -> None:
    """2026-09-22 18:40: цикл самопочинки ушёл на «в пробе нет numpy» — это
    решение о среде, кодом не закрывается."""
    from core.patch_route import OPERATOR_DECISION
    from core.self_improvement_issues import SelfImprovementIssue

    root = _repo(tmp_path)
    registry = SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH)
    registry._save([SelfImprovementIssue(
        fingerprint="no-numpy", title="в пробе нет numpy", action=OPERATOR_DECISION, status="open",
        first_seen="2026-09-22T00:00:00+00:00", last_seen="2026-09-22T00:00:00+00:00",
        evidence=("ModuleNotFoundError",), related_files=(), related_error_text="",
        suggested_next_action="решает оператор")])

    assert defect_goal(root) is None


def test_a_defect_without_a_task_is_not_given_to_the_hands(tmp_path: Path) -> None:
    """Дефект без названной задачи правкой не закрывается — он её и не получит.

    Замер ночи на 2026-09-23: из шестнадцати открытых дефектов трое были с
    пустым `suggested_next_action`, и ровно они дали три последние красные
    правки подряд. Цель выходила «Почини дефект X. Что делать: » — агент, не
    зная, что менять, писал отписку, слипшиеся разделители на 305 КБ и тест
    поверх уже существующего файла. Такому дефекту нужна сначала формулировка.
    """
    from core.patch_route import LOG_RELPATH, defect_goal
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry
    from core.state_integrity import (
        read_state_jsonl_unlocked,
        rewrite_state_jsonl_unlocked,
    )

    registry = SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH)
    issue = registry.upsert_failure("дефект без задачи", "2026-09-23T01:00:00+00:00")
    # Поле обнуляется прямо в хранилище: так эти три дефекта и лежат в живом
    # реестре — их заводили руками, и задачу никто не назвал.
    path = tmp_path / DEFAULT_ISSUE_PATH
    rows = read_state_jsonl_unlocked(path)
    for row in rows:
        row["suggested_next_action"] = ""
    rewrite_state_jsonl_unlocked(path, rows)
    assert not (SelfImprovementIssueRegistry(path).unresolved()[0].suggested_next_action or "")

    assert defect_goal(tmp_path) is None, "дефект без задачи ушёл в руки вслепую"

    # Пропуск не молчит: человеку видно, какому дефекту нужна формулировка.
    log = (tmp_path / LOG_RELPATH).read_text(encoding="utf-8")
    assert "defect_without_a_task_skipped" in log
    assert issue.fingerprint in log
