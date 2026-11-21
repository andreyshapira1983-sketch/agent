"""Зелёный patch_check — без новых ошибок ruff, и копия лежит там, где её читает песочница.

27.09 (правка агента pdf_text_web_fetch): вердикт green, а в поле ruff — «Found
2 errors»; вердикт ruff не читал. Полный прогон той же проверки на сервере был
красным всегда: копия в /tmp (0700) — песочница проб её не читала, а сторож
записи считает /tmp своим; два теста проб падали и на чистом коде.
"""
from __future__ import annotations

from pathlib import Path

from tests.test_a_patch_test_must_witness_the_change import _repo
from tools.patch_check import PatchCheckTool

_WITNESS = ("FILE: tests/test_calc_new.py\n<<<<<<< SEARCH\n=======\n"
            "from mod.calc import add\n\n\ndef test_it():\n    assert add(2, 2) == 4\n>>>>>>> REPLACE\n")


def _check(root: Path, calc_block: str) -> dict:
    rel = "proposals/selffix/x/edits.txt"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(calc_block + _WITNESS, encoding="utf-8")
    return PatchCheckTool(workspace_root=root).run(rel)


def test_a_patch_that_adds_a_lint_error_is_red(tmp_path: Path) -> None:
    _repo(tmp_path)
    result = _check(tmp_path, "FILE: mod/calc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a - b\n"
                              "=======\nimport os\n\n\ndef add(a, b):\n    return a + b\n>>>>>>> REPLACE\n")
    assert result["ruff_new_errors"] == 1, result.get("ruff")
    assert result["verdict"] == "red" and "ruff" in result["why"]
