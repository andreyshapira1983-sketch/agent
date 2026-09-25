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

from core.control_files import CONTROL_RELPATHS, control_file_hit
from core.patch_route import _forbidden
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


def test_raising_the_spend_limit_is_refused_after_any_approval(ws: Path) -> None:
    before = (ws / "config" / "budget_limits.json").read_text(encoding="utf-8")
    with pytest.raises(PermissionError, match=r"config/budget_limits\.json"):
        FileWriteTool(workspace_root=ws).run(path="config/budget_limits.json",
                                             content='{"day": {"llm_calls": 999999}}')
    assert (ws / "config" / "budget_limits.json").read_text(encoding="utf-8") == before
    assert not list((ws / "config").glob("*.bak.*"))


@pytest.mark.parametrize("spelling", ["./data//budget_kill_switch.json",
                                      "config/../data/budget_kill_switch.json",
                                      "data/budget_kill_switch.json.tmp"])
def test_another_spelling_of_the_same_brake_is_the_same_brake(ws: Path, spelling: str) -> None:
    with pytest.raises(PermissionError, match="operator-only"):
        FileWriteTool(workspace_root=ws).run(path=spelling, content="{}")


def test_an_ordinary_file_next_to_the_brakes_is_still_writable(ws: Path) -> None:
    out = FileWriteTool(workspace_root=ws).run(path="config/my_notes.json", content="{}")
    assert (ws / "config" / "my_notes.json").read_text(encoding="utf-8") == "{}"
    assert out["mode"] == "create"
    assert control_file_hit(ws, ws / "data" / "budget_kill_switch.json.bak") is None


def test_touch_cannot_plant_an_empty_kill_switch(ws: Path) -> None:
    (ws / "data").mkdir()
    with pytest.raises(PermissionError, match="operator-only"):
        ShellExecTool(workspace_root=ws).run(["touch", "data/budget_kill_switch.json"])
    assert not (ws / "data" / "budget_kill_switch.json").exists()


def test_the_route_without_a_human_cannot_touch_the_brakes_or_their_list() -> None:
    paths = ["config/budget_limits.json", "data/patch_route_state.json",
             "core/control_files.py", "core/loop_attempt.py"]
    assert _forbidden(paths) == paths[:3]
