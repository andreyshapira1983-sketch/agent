"""Агент проверяет свою правку сам, в отдельной копии (tools/patch_check.py).

2026-09-22, задача «банк уроков»: правка дважды несла кусок кода, набранный по
памяти, и агент узнавал об этом ходом позже от Claude. Инструмент возвращает
ошибку со строками файла и вывод тестов в том же ходе, не трогая рабочую папку.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tools.patch_check import PatchCheckTool, apply_blocks, parse_blocks


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("def f(\n    *,\n    x: int = 1,\n) -> int:\n    return x\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=root, check=True)  # noqa: S603 — свои аргументы
    return root


def _patch(root: Path, text: str) -> str:
    p = root / "proposals" / "edits.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return "proposals/edits.txt"


def test_a_search_typed_from_memory_is_answered_with_the_real_lines(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    blocks = parse_blocks("FILE: pkg/mod.py\n<<<<<<< SEARCH\ndef f(\n    x: int,\n=======\ndef g(\n>>>>>>> REPLACE\n")
    errors = apply_blocks(root, blocks)
    assert len(errors) == 1 and "найден 0 раз" in errors[0]
    assert "    2|     *," in errors[0], "агенту показаны настоящие строки файла, чтобы скопировать"


def test_a_good_patch_is_applied_and_tested_outside_the_workspace(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    rel = _patch(root, (
        "FILE: pkg/mod.py\n<<<<<<< SEARCH\n    return x\n=======\n    return x + 1\n>>>>>>> REPLACE\n"
        "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n=======\n"
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f(x=1) == 2\n>>>>>>> REPLACE\n"))

    result = PatchCheckTool(workspace_root=root).run(path=rel)

    assert result["applied"] is True and result["errors"] == []
    assert result["tests_exit_code"] == 0, result["tests_output"]
    assert "return x\n" in (root / "pkg" / "mod.py").read_text(encoding="utf-8"), "рабочая папка не тронута"
    assert not (root / "tests").exists()


def test_a_patch_without_a_test_is_told_so(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    rel = _patch(root, "FILE: pkg/mod.py\n<<<<<<< SEARCH\n    return x\n=======\n    return x + 1\n>>>>>>> REPLACE\n")
    result = PatchCheckTool(workspace_root=root).run(path=rel)
    assert result["tests_exit_code"] is None and "proves nothing" in result["tests_output"]


def test_lines_are_replaced_by_number_without_copying_old_text(tmp_path: Path) -> None:
    """2026-09-22 14:37: модель трижды не смогла переписать сигнатуру символ в
    символ, хотя видела её; номера строк из file_read она видит без ошибок."""
    root = _repo(tmp_path)
    rel = _patch(root, (
        "FILE: pkg/mod.py\n<<<<<<< LINES 1-4\ndef f(*, x: int = 1, y: int = 0) -> int:\n>>>>>>> REPLACE\n"
        "FILE: pkg/mod.py\n<<<<<<< LINES 5-5\n    return x + y\n>>>>>>> REPLACE\n"
        "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n=======\n"
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f(x=1, y=2) == 3\n>>>>>>> REPLACE\n"))

    result = PatchCheckTool(workspace_root=root).run(path=rel)

    assert result["applied"] is True, result["errors"]
    assert result["tests_exit_code"] == 0, result["tests_output"]
    assert "+def f(*, x: int = 1, y: int = 0) -> int:" in result["diff"]


def test_a_line_range_outside_the_file_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    errors = apply_blocks(root, parse_blocks("FILE: pkg/mod.py\n<<<<<<< LINES 4-99\nx\n>>>>>>> REPLACE\n"))
    assert errors and "4-99" in errors[0]


def test_a_patch_that_changes_nothing_is_not_green(tmp_path: Path) -> None:
    """2026-09-22 14:43: строки 284–292 заменены теми же строками, без теста, —
    applied=True, и ход закрылся как сделанный. Ничего не изменить — не зелёное."""
    root = _repo(tmp_path)
    same = (root / "pkg" / "mod.py").read_text(encoding="utf-8").splitlines()[0]
    rel = _patch(root, f"FILE: pkg/mod.py\n<<<<<<< LINES 1-1\n{same}\n>>>>>>> REPLACE\n")
    result = PatchCheckTool(workspace_root=root).run(path=rel)
    assert result["applied"] is True
    assert result["verdict"] == "red" and "changes nothing" in result["why"]


def test_only_a_change_with_a_passing_test_is_green(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    rel = _patch(root, (
        "FILE: pkg/mod.py\n<<<<<<< LINES 5-5\n    return x + 1\n>>>>>>> REPLACE\n"
        "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n=======\n"
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f(x=1) == 2\n>>>>>>> REPLACE\n"))
    assert PatchCheckTool(workspace_root=root).run(path=rel)["verdict"] == "green"
