"""Предсказание помощника сверяется с тем, что он вернул (core/subagent_predictions.py).

Журнал оператора «Память и под-агенты»: текст «почему» — слабая защита, сильная
— проверяемое предсказание, сверенное с фактом; завышение ожиданий видно в сводке.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.subagent_predictions import RELPATH, compare, predicted_sources, tally
from core.subagent_runner import SubAgentRunResult
from tools.base import ToolRegistry


def _result(**overrides) -> SubAgentRunResult:
    values = {"contract_name": "PaperFinder", "role": "researcher", "objective": "find papers",
              "answer": "two papers ...", "trace_id": "trace_child_7", "status": "success",
              "external_evidence_count": 1}
    values.update(overrides)
    return SubAgentRunResult(**values)


@pytest.fixture()
def tool(tmp_path: Path):
    from tools.spawn_subagent import SpawnSubagentTool

    made = SpawnSubagentTool(workspace_root=tmp_path, policy=MagicMock(), model_router=MagicMock(),
                             parent_registry=ToolRegistry(), log_dir=tmp_path)
    made._runner.run = MagicMock(return_value=_result())  # type: ignore[method-assign]
    return made


@pytest.mark.parametrize(("expect", "stated", "want"), [
    ("3 papers with URLs and years", None, (3, "parsed")),
    ("3-5 principles and 2 limitations", None, (3, "parsed")),
    ("a summary of the failures", None, (None, "unchecked")),
    ("whatever the text says", 2, (2, "stated")),
])
def test_the_promise_is_a_number(expect: str, stated, want) -> None:
    assert predicted_sources(expect, stated) == want


def test_a_broken_promise_is_recorded_as_missed_with_the_facts(tool, tmp_path: Path) -> None:
    (tmp_path / "trace_child_7.jsonl").write_text(
        '{"event": "tool_call"}\n{"event": "tool_result"}\n{"event": "tool_call"}\n', encoding="utf-8")
    tool.run(role="researcher", objective="find 3 papers on agent testing", why="independent of the test run",
             expect="3 papers with titles, years and URLs")
    [row] = [json.loads(x) for x in (tmp_path / RELPATH).read_text(encoding="utf-8").splitlines()]
    assert (row["expected_sources"], row["prediction"], row["sources"], row["tool_calls"], row["met"]) \
        == (3, "parsed", 1, 2, False)


def test_a_kept_promise_and_the_tally(tool, tmp_path: Path) -> None:
    tool.run(role="researcher", objective="find a paper", why="w", expect="the paper", expect_sources=1)
    tool.run(role="researcher", objective="find papers", why="w", expect="3 papers")
    tool.run(role="reader", objective="read the file", why="w", expect="a summary of it")
    stats = tally(tmp_path)
    assert (stats["runs"], stats["checkable"], stats["met"]) == (3, 2, 1)
    assert (stats["promised_sources"], stats["delivered_sources"]) == (4, 2)
    assert stats["missed_by_role"] == {"researcher": 1}


def test_a_failed_run_never_counts_as_a_kept_promise() -> None:
    assert compare(0, 0, "error") is False
    assert compare(None, 5, "success") is None
