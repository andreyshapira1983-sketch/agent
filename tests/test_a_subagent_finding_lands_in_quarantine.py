"""Находка субагента остаётся уликой: записана, но влиять не вправе.

Замер, отвергнутые варианты и границы: MIR-157 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import ast
import pathlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.subagent_quarantine import (
    QUARANTINE_AUTHORITY,
    load_quarantine,
    quarantine_status_lines,
)
from core.subagent_runner import SubAgentRunResult
from tools.base import ToolRegistry


def _result(**overrides) -> SubAgentRunResult:
    values = {
        "contract_name": "WebResearcher",
        "role": "researcher",
        "objective": "выяснить, что такое MetaGPT",
        "answer": "MetaGPT — мультиагентный фреймворк",
        "trace_id": "trace_child_1",
        "status": "success",
    }
    values.update(overrides)
    return SubAgentRunResult(**values)


@pytest.fixture()
def tool(tmp_path: Path):
    from tools.spawn_subagent import SpawnSubagentTool

    made = SpawnSubagentTool(
        workspace_root=tmp_path,
        policy=MagicMock(),
        model_router=MagicMock(),
        parent_registry=ToolRegistry(),
        log_dir=tmp_path,
    )
    made._runner.run = MagicMock(return_value=_result())  # type: ignore[method-assign]
    return made


def test_the_finding_is_written_down_with_its_origin(tool, tmp_path: Path) -> None:
    """Красный свидетель: находка жила только в трассе и ничем не спрашивалась.

    Долговременной записи не существовало: субагент отвечал, строка уходила в
    цепочку улик родителя и исчезала вместе с прогоном.
    """
    tool.run(role="researcher", objective="выяснить, что такое MetaGPT", why="отдельная область", expect="описание MetaGPT с источником")

    rows = load_quarantine(tmp_path)
    assert len(rows) == 1, "находка субагента нигде не записана"
    row = rows[0]
    assert row["objective"] == "выяснить, что такое MetaGPT"
    assert row["trace_id"] == "trace_child_1"
    assert row["contract_name"] and row["role"]


def test_the_record_declares_that_it_may_not_influence_anything(tool, tmp_path: Path) -> None:
    """Полномочие названо явно: умолчание однажды уже раздало голос человека."""
    tool.run(role="researcher", objective="выяснить, что такое MetaGPT", why="отдельная область", expect="описание MetaGPT с источником")

    assert load_quarantine(tmp_path)[0]["authority"] == QUARANTINE_AUTHORITY == "none"


def test_a_secret_in_the_finding_does_not_land_raw(tmp_path: Path) -> None:
    """Субагент читает файлы; его ответ может принести ключ."""
    from core.subagent_quarantine import quarantine_finding

    quarantine_finding(
        tmp_path,
        _result(answer="нашёл sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 в конфиге"),
    )

    stored = load_quarantine(tmp_path)[0]["answer"]
    assert "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in stored
    assert "REDACTED" in stored


def test_quarantine_is_visible_to_the_operator(tmp_path: Path) -> None:
    """Карантин без читателя — это MIR-138 заново, поэтому читатель обязателен."""
    from core.subagent_quarantine import quarantine_finding

    assert quarantine_status_lines(tmp_path) == [], "пустой карантин не о чем докладывать"

    quarantine_finding(tmp_path, _result())
    lines = quarantine_status_lines(tmp_path)

    assert lines and "1 finding" in lines[0]
    assert "may influence" in lines[0], "строка обязана называть, что права у находки нет"


def test_nothing_else_reads_the_quarantine(tmp_path: Path) -> None:
    """Граница, на которой стоит смысл карантина.

    Если запись начнёт читать подбор памяти, планировщик или верификатор — она
    перестанет быть уликой без прав и станет памятью, никем не продвинутой.
    """
    allowed = {"subagent_quarantine.py", "agent_tick.py"}
    core_dir = pathlib.Path(__file__).resolve().parents[1] / "core"
    readers: set[str] = set()
    for path in sorted(core_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        names = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if {"load_quarantine", "quarantine_path"} & names and path.name not in allowed:
            readers.add(path.name)

    assert not readers, (
        "карантин читают помимо своего модуля и строки состояния: "
        + ", ".join(sorted(readers))
    )


def test_a_subagent_needs_a_reason_and_a_prediction(tool, tmp_path: Path) -> None:
    """Правило оператора 2026-09-25: помощника создавать можно, но письменно —
    зачем он, а не прямой вызов, и что вернёт. Ночь 24.09: 37 помощников в трёх
    ходах поиска вакансий дали 0–1 объявление, и ни одно «зачем» не записано."""
    import json

    from core.step_sanitizer import sanitize_step

    warnings: list[str] = []
    bare = sanitize_step("spawn_subagent", {"role": "UpworkJobScout", "objective": "find jobs"},
                         None, 0, warnings)
    assert bare is None and "requires 'why'" in warnings[-1]
    with pytest.raises(ValueError, match="why"):
        tool.run(role="researcher", objective="выяснить, что такое MetaGPT")

    tool.run(role="researcher", objective="выяснить, что такое MetaGPT",
             why="отдельная область", expect="описание MetaGPT с источником")
    rows = [json.loads(line) for line in
            (tmp_path / "data" / "subagent_predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["expect"] == "описание MetaGPT с источником"
    assert rows[-1]["status"] == "success"
