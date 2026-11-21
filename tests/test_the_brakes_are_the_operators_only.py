"""Тормоза агента меняет только оператор (core/control_files.py).

До 2026-09-25 выключатель бюджета и лимиты берегли лишь ворота одобрения:
новый файл шёл без вопроса, перезапись — к человеку. `run()` инструмента
исполняется ПОСЛЕ одобрения, поэтому отказ в нём и есть «одобрение не
снимает запрет».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.control_files import CONTROL_RELPATHS
from tools.file_write import FileWriteTool
from tools.shell_exec import ShellExecTool


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "budget_limits.json").write_text('{"day": {"llm_calls": 50}}', encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("rel", CONTROL_RELPATHS)
def test_a_brake_that_is_not_on_disk_yet_cannot_be_created(ws: Path, rel: str) -> None:
    if (ws / rel).exists():
        (ws / rel).unlink()
    tool = FileWriteTool(workspace_root=ws)
    assert tool.risk_for({"path": rel, "content": "{}"}) == "irreversible"
    with pytest.raises(PermissionError, match="operator-only control file"):
        tool.run(path=rel, content=json.dumps({"active": False}))
    assert not (ws / rel).exists()


@pytest.mark.parametrize("spelling", ["./data//budget_kill_switch.json",
                                      "config/../data/budget_kill_switch.json",
                                      "data/budget_kill_switch.json.tmp"])
def test_another_spelling_of_the_same_brake_is_the_same_brake(ws: Path, spelling: str) -> None:
    with pytest.raises(PermissionError, match="operator-only"):
        FileWriteTool(workspace_root=ws).run(path=spelling, content="{}")


def test_touch_cannot_plant_an_empty_kill_switch(ws: Path) -> None:
    (ws / "data").mkdir()
    with pytest.raises(PermissionError, match="operator-only"):
        ShellExecTool(workspace_root=ws).run(["touch", "data/budget_kill_switch.json"])
    assert not (ws / "data" / "budget_kill_switch.json").exists()
