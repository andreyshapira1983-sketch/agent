"""patch_check не пропускает подгонку под тест.

Замер 2026-09-24: «зелёная» правка агента (маршрут «цитат») прошла только
свои два теста — и оба проходили на СТАРОМ коде, то есть ничего не
свидетельствовали, — а ломала 5 соседних тестов маршрута, которых
patch_check не запускал. Теперь новый тест обязан падать на старом коде, а
тесты, импортирующие изменённый модуль, идут в прогон сами.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tools.patch_check import PatchCheckTool


def _repo(root: Path, existing_test: str = "") -> None:
    (root / "mod").mkdir()
    (root / "mod" / "__init__.py").write_text("", encoding="utf-8")
    (root / "mod" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    if existing_test:
        (root / "tests" / "test_calc_old.py").write_text(existing_test, encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)  # noqa: S603 — свои git-команды во временной папке


def _patch(root: Path, test_body: str) -> str:
    rel = "proposals/selffix/x/edits.txt"
    (root / "proposals/selffix/x").mkdir(parents=True)
    (root / rel).write_text(
        "FILE: mod/calc.py\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n"
        f"FILE: tests/test_calc_new.py\n<<<<<<< SEARCH\n=======\n{test_body}>>>>>>> REPLACE\n",
        encoding="utf-8")
    return rel


def test_a_test_that_passes_on_old_code_is_red(tmp_path: Path) -> None:
    """Ломалось здесь: такой тест давал green."""
    _repo(tmp_path)
    rel = _patch(tmp_path, "from mod.calc import add\n\n\ndef test_it():\n    assert callable(add)\n")
    result = PatchCheckTool(workspace_root=tmp_path).run(rel)
    assert result["verdict"] == "red", result
    assert "OLD code" in result["why"]


def test_a_real_witness_is_green(tmp_path: Path) -> None:
    _repo(tmp_path)
    rel = _patch(tmp_path, "from mod.calc import add\n\n\ndef test_it():\n    assert add(2, 2) == 4\n")
    result = PatchCheckTool(workspace_root=tmp_path).run(rel)
    assert result["verdict"] == "green", result
    assert result["witness_exit_code"] != 0


def test_a_broken_neighbor_is_run_and_turns_it_red(tmp_path: Path) -> None:
    """Ломалось здесь: соседний тест модуля не запускался, и поломка проходила."""
    _repo(tmp_path, existing_test="from mod.calc import add\n\n\ndef test_old():\n    assert add(3, 2) == 1\n")
    rel = _patch(tmp_path, "from mod.calc import add\n\n\ndef test_it():\n    assert add(2, 2) == 4\n")
    result = PatchCheckTool(workspace_root=tmp_path).run(rel)
    assert result["neighbor_tests"] == ["tests/test_calc_old.py"]
    assert result["verdict"] == "red" and result["why"] == "tests are not green", result
