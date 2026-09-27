"""patch_check говорит, где лежит правка: во временной копии, не в файле.

След 27.09 (trace_8d7083df): после красной проверки с «applied: True» агент
написал добавку к своей прошлой попытке — убрать `# noqa: S603`, которого в
живом файле нет, — и четыре круга получал «кусок SEARCH найден 0 раз».
"""
from __future__ import annotations

from pathlib import Path

from tests.test_a_patch_test_must_witness_the_change import _repo
from tools.patch_check import PatchCheckTool


def _write(root: Path, text: str) -> str:
    rel = "proposals/selffix/x/edits.txt"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")
    return rel


def test_the_second_check_starts_from_the_file_on_disk_and_says_so(tmp_path: Path) -> None:
    _repo(tmp_path)
    first = _write(tmp_path, "FILE: mod/calc.py\n<<<<<<< SEARCH\n    return a - b\n=======\n"
                             "    return a + b  # noqa: S603\n>>>>>>> REPLACE\n")
    tool = PatchCheckTool(workspace_root=tmp_path)
    one = tool.run(first)
    assert one["applied"] is True
    assert "no file on disk changed" in one["where"], "«applied» — про копию, и ответ это называет"
    assert list(one)[:4] == ["verdict", "why", "applied", "where"], "рядом с applied, не в хвосте"
    assert (tmp_path / "mod" / "calc.py").read_text(encoding="utf-8").endswith("return a - b\n")

    delta = _write(tmp_path, "FILE: mod/calc.py\n<<<<<<< SEARCH\n    return a + b  # noqa: S603\n=======\n"
                             "    return a + b\n>>>>>>> REPLACE\n")
    two = tool.run(delta)
    assert two["verdict"] == "red" and two["applied"] is False
    assert "WHOLE change" in two["where"], "добавка к прошлой попытке не ляжет — и ответ говорит почему"
