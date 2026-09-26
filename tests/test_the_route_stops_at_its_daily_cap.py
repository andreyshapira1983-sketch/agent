"""Самопочинка без человека ставит не больше DAILY_CAP правок в сутки."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core import patch_route
from core.patch_route import DAILY_CAP, LOG_RELPATH, STATE_RELPATH, settle_patch
from tests.test_the_agent_repairs_itself_without_a_human import _repo

_GOAL = "Файл proposals/selffix/d/edits.txt создан"
_GREEN_PATCH = (
    "FILE: core/mod.py\n<<<<<<< LINES 2-2\n    return 2\n>>>>>>> REPLACE\n"
    "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n=======\nfrom core.mod import f\n\n\n"
    "def test_f():\n    assert f() == 2\n>>>>>>> REPLACE\n"
)


def _prepare(tmp_path: Path, applied_today: int) -> Path:
    root = _repo(tmp_path)
    today = patch_route._now().date().isoformat()
    state = root / STATE_RELPATH
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"attempted": {}, "applied": {today: applied_today}}), encoding="utf-8")
    patch = root / "proposals" / "selffix" / "d" / "edits.txt"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(_GREEN_PATCH, encoding="utf-8")
    return root


def _head(root: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,  # noqa: S607
                          capture_output=True, text=True).stdout


def test_a_green_patch_over_the_daily_cap_does_not_land(tmp_path: Path) -> None:
    """Правка сверх дневного потолка не ставится: код и HEAD не тронуты, журнал помнит."""
    root = _prepare(tmp_path, DAILY_CAP)
    head = _head(root)

    verdict = settle_patch(None, root, _GOAL)

    assert verdict["verdict"] == "missing", verdict
    assert "потолок" in verdict["reason"]
    assert "return 1" in (root / "core" / "mod.py").read_text(encoding="utf-8")
    assert _head(root) == head
    rows = [json.loads(line) for line in (root / LOG_RELPATH).read_text(encoding="utf-8").splitlines()]
    assert [r["result"] for r in rows] == ["daily_cap"], rows


def test_one_below_the_cap_the_same_patch_lands(tmp_path: Path) -> None:
    """Граница: на одну правку ниже потолка та же правка ставится."""
    root = _prepare(tmp_path, DAILY_CAP - 1)

    verdict = settle_patch(None, root, _GOAL)

    assert verdict["verdict"] == "verified", verdict
    assert "return 2" in (root / "core" / "mod.py").read_text(encoding="utf-8")
