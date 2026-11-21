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


def test_text_outside_the_blocks_is_named_not_dropped(tmp_path: Path) -> None:
    """2026-09-22 14:54: новый код стоял ПОСЛЕ закрывающей метки блока LINES —
    четыре круга подряд он пропадал молча, и ошибка не менялась."""
    root = _repo(tmp_path)
    rel = _patch(root, "FILE: pkg/mod.py\n<<<<<<< LINES 5-5\n    return x\n>>>>>>> REPLACE\n    return x + 1\n")
    result = PatchCheckTool(workspace_root=root).run(path=rel)
    assert result["verdict"] == "red" and "return x + 1" in result["errors"][0]


def test_the_full_suite_waits_for_the_own_test_to_be_green(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    rel = _patch(root, (
        "FILE: tests/test_red.py\n<<<<<<< SEARCH\n=======\ndef test_red():\n    assert False\n>>>>>>> REPLACE\n"))
    result = PatchCheckTool(workspace_root=root).run(path=rel, full=True)
    assert result["tests_exit_code"] == 1 and "full_exit_code" not in result
    assert "make them green first" in result["full_output"]
