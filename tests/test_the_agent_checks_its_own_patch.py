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
