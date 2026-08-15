"""Свежая трасса без ответа хуже старой трассы с ответом.

Background: docs/CODE_NOTES.md, "Recency is not relevance".
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tools.read_logs import MAX_TRACE_SCAN, ReadLogsTool


def _write_trace(log_dir: Path, stem: str, events: list[dict], *, age: int) -> None:
    path = log_dir / f"{stem}.jsonl"
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8",
    )
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def _tool(tmp_path: Path, live: str | None = None) -> ReadLogsTool:
    (tmp_path / "logs").mkdir(exist_ok=True)
    return ReadLogsTool(tmp_path, live_trace_id=live)


_ERROR = {"event": "error", "payload": {"message": "boom"}}
_NOISE = {"event": "session_start", "payload": {}}


def test_the_measured_shape_a_filter_reaches_past_an_empty_newest_trace(tmp_path: Path):
    """Живой случай 2026-08-15: автономный прогон спросил ошибки, получил
    новейшую трассу — пять служебных строк сессии, где оператор кликал по
    одобрениям, — и встал: «нужные для диагностики события недоступны».
    Ошибки лежали в соседней трассе.
    """
    tool = _tool(tmp_path)
    _write_trace(tool.log_dir, "trace_old", [_NOISE, _ERROR], age=600)
    _write_trace(tool.log_dir, "trace_new", [_NOISE] * 5, age=60)

    result = tool.run(event_filter=["error"])

    assert result["trace_id"] == "trace_old"
    assert result["events_returned"] == 1
    assert result["traces_searched"] == 2


def test_no_match_anywhere_is_said_honestly(tmp_path: Path):
    """«Нет нигде» и «нет в этой» — разные ответы, и различает их
    `traces_searched`: пусто при >1 означает, что смотрели всю недавнюю
    историю, а не один файл.
    """
    tool = _tool(tmp_path)
    _write_trace(tool.log_dir, "trace_a", [_NOISE], age=600)
    _write_trace(tool.log_dir, "trace_b", [_NOISE], age=60)

    result = tool.run(event_filter=["error"])

    assert result["events_returned"] == 0
    assert result["traces_searched"] == 2
    assert result["trace_id"] == "trace_b"  # новейшая — как и раньше


def test_an_explicit_trace_id_is_an_address_not_a_wish(tmp_path: Path):
    """Явный адрес отвечает про себя, пустой или нет: подменить его «более
    подходящей» трассой значило бы отвечать не на заданный вопрос.
    """
    tool = _tool(tmp_path)
    _write_trace(tool.log_dir, "trace_empty", [_NOISE], age=600)
    _write_trace(tool.log_dir, "trace_rich", [_ERROR], age=60)

    result = tool.run(event_filter=["error"], trace_id="trace_empty")

    assert result["trace_id"] == "trace_empty"
    assert result["events_returned"] == 0
    assert result["traces_searched"] == 1


def test_without_a_filter_the_newest_past_trace_still_wins(tmp_path: Path):
    """Улов не отдан: без фильтра «уместность» не определена, и новейшая
    прошлая трасса остаётся правильным ответом.
    """
    tool = _tool(tmp_path)
    _write_trace(tool.log_dir, "trace_old", [_ERROR], age=600)
    _write_trace(tool.log_dir, "trace_new", [_NOISE], age=60)

    result = tool.run()

    assert result["trace_id"] == "trace_new"


def test_the_live_trace_is_still_never_chosen(tmp_path: Path):
    """MIR-089 не откатился: своя живая трасса не источник — исхода этого
    прогона в ней ещё нет, даже если запрошенное там уже есть.
    """
    tool = _tool(tmp_path, live="trace_live")
    _write_trace(tool.log_dir, "trace_past", [_NOISE], age=600)
    _write_trace(tool.log_dir, "trace_live", [_ERROR], age=10)

    result = tool.run(event_filter=["error"])

    assert result["trace_id"] == "trace_past"
    assert result["is_live_session"] is False


def test_the_scan_is_bounded(tmp_path: Path):
    """Дальше `MAX_TRACE_SCAN` — археология, ей положен явный trace_id."""
    tool = _tool(tmp_path)
    for i in range(MAX_TRACE_SCAN + 4):
        _write_trace(tool.log_dir, f"trace_{i:03d}", [_NOISE], age=60 + i)

    result = tool.run(event_filter=["error"])

    assert result["traces_searched"] == MAX_TRACE_SCAN


def test_unseen_filter_names_work_the_same_way(tmp_path: Path):
    """Форма, под которую правило не подгоняли: другой фильтр, тот же класс —
    запрошенное лежит не в новейшем файле.
    """
    tool = _tool(tmp_path)
    _write_trace(tool.log_dir, "trace_old", [{"event": "replan", "payload": {}}], age=600)
    _write_trace(tool.log_dir, "trace_new", [_NOISE], age=60)

    result = tool.run(event_filter=["replan"])

    assert result["trace_id"] == "trace_old"
    assert result["events_returned"] == 1
