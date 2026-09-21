"""Запись в журнал несёт полный момент времени, а не дату без зоны.

Эпизод 2026-09-20: агент записал в `data/watchdog_checks.jsonl`
`"ts": "2026-09-20"`. `journal_append` принял, и замер живого состояния
(`tests/test_a_naive_timestamp_is_read_as_utc.py`) покраснел на одной строке
из 28816. Корень — дверь, а не строка: инструмент пропускал любое время.
"""
from __future__ import annotations

import pytest

from tools.journal_append import JournalAppendTool


def _tool(tmp_path):
    (tmp_path / "data").mkdir()
    return JournalAppendTool(workspace_root=tmp_path)


@pytest.mark.parametrize("stamp", ["2026-09-20", "2026-09-20T17:17:00"])
def test_a_stamp_without_a_zone_is_refused(tmp_path, stamp) -> None:
    tool = _tool(tmp_path)
    with pytest.raises(ValueError, match="часового пояса"):
        tool.run(path="data/watchdog_checks.jsonl", record={"check": "git", "ts": stamp})
    assert not (tmp_path / "data" / "watchdog_checks.jsonl").exists()


@pytest.mark.parametrize("stamp", ["2026-09-20T17:17:00+00:00", "2026-09-20T17:17:00Z",
                                   "2026-09-20T20:17:00+03:00"])
def test_a_stamp_with_a_zone_is_written(tmp_path, stamp) -> None:
    tool = _tool(tmp_path)
    result = tool.run(path="data/watchdog_checks.jsonl", record={"check": "git", "ts": stamp})
    assert result["appended"] is True


def test_a_field_that_is_not_a_stamp_is_left_alone(tmp_path) -> None:
    tool = _tool(tmp_path)
    result = tool.run(path="data/watchdog_checks.jsonl",
                      record={"check": "git", "commit_date": "Sun Sep 20 20:11:26 2026"})
    assert result["appended"] is True
