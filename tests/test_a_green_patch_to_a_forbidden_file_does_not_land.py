"""Зелёная правка, задевающая запретное, не ставится: вердикт «missing», код не тронут, журнал помнит."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.patch_route import LOG_RELPATH, settle_patch
from tests.test_the_agent_repairs_itself_without_a_human import _repo

_GOAL = "Файл proposals/selffix/d/edits.txt создан"
_FORBIDDEN_FILE = "core/budget_limit.py"
_BEFORE = "def cap():\n    return 1\n"


def _commit(root: Path, rel: str, content: str) -> None:
    (root / rel).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S607
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "setup"],  # noqa: S607
                   cwd=root, check=True)


def _patch(root: Path, text: str) -> None:
    path = root / "proposals" / "selffix" / "d" / "edits.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _log_rows(root: Path) -> list[dict]:
    path = root / LOG_RELPATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_green_patch_touching_a_forbidden_file_is_stopped(tmp_path: Path) -> None:
    """Правка запретного файла останавливается, даже когда её проверка зелёная."""
    root = _repo(tmp_path)
    _commit(root, _FORBIDDEN_FILE, _BEFORE)
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,  # noqa: S607
                                 capture_output=True, text=True).stdout
    _patch(root, f"FILE: {_FORBIDDEN_FILE}\n<<<<<<< LINES 2-2\n    return 2\n>>>>>>> REPLACE\n"
                 "FILE: tests/test_cap.py\n<<<<<<< SEARCH\n=======\nfrom core.budget_limit import cap\n\n\n"
                 "def test_cap():\n    assert cap() == 2\n>>>>>>> REPLACE\n")

    verdict = settle_patch(None, root, _GOAL)

    assert verdict["verdict"] == "missing", verdict
    assert "запретное" in verdict["reason"] and _FORBIDDEN_FILE in verdict["reason"]
    assert (root / _FORBIDDEN_FILE).read_text(encoding="utf-8") == _BEFORE
    assert not (root / "tests" / "test_cap.py").exists()
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,  # noqa: S607
                          capture_output=True, text=True).stdout == head_before
    rows = _log_rows(root)
    assert [r["result"] for r in rows] == ["forbidden"], rows
    assert rows[0]["files"] == [_FORBIDDEN_FILE]
