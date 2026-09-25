"""Запись памяти, называющая исчезнувший код, доходит до агента с пометкой.

Замер 2026-09-25 на сервере: 36 ссылок памяти на файлы, которых больше нет
(раскол, переименование, откат). Сверка — в момент чтения (core/code_citations.py,
по arXiv 2609.25130: «держится ли это утверждение», хранимое не уничтожается).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.code_citations import annotate, stale_refs
from core.failure_cards import CARDS_RELPATH, note_for, signature
from core.loop_memory_read import AgentLoopMemoryRead


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "alive.py").write_text("LIMIT = 3\n\ndef run():\n    return LIMIT\n", encoding="utf-8")
    return tmp_path


def test_a_vanished_file_and_a_vanished_name_are_named(ws: Path) -> None:
    text = ("fixed in core/confidence_gate.py; see core/alive.py::run and core/alive.py:gone_helper, "
            "LIMIT at core/alive.py::LIMIT")
    assert stale_refs(ws, text) == ["core/confidence_gate.py (файла больше нет)",
                                    "core/alive.py::gone_helper (имени в файле больше нет)"]


@pytest.mark.parametrize("text", ["the cap lives in core/alive.py::LIMIT (line 1)",
                                  "reader (core/alive.py:N — a masked line number)",
                                  "core/alive.py:114 — a plain line number"])
def test_a_record_whose_code_still_exists_is_left_exactly_as_it_was(ws: Path, text: str) -> None:
    assert annotate(ws, text) is text


def test_the_record_is_kept_and_marked_not_deleted(ws: Path) -> None:
    out = annotate(ws, "use core/model_router_helpers.py to pick a model")
    assert out.startswith("use core/model_router_helpers.py to pick a model")
    assert "АДРЕС УСТАРЕЛ: core/model_router_helpers.py (файла больше нет)" in out


def test_a_past_lesson_shown_at_failure_carries_the_mark(ws: Path) -> None:
    sig = signature("file_read", "FileNotFoundError: core/old.py")
    card = {"sig": sig, "tool": "file_read", "error": "x", "status": "active",
            "lesson": "read core/confidence_gate.py first"}
    (ws / CARDS_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (ws / CARDS_RELPATH).write_text(json.dumps(card, ensure_ascii=False) + "\n", encoding="utf-8")
    note = note_for(ws, "file_read", "FileNotFoundError: core/old.py")
    assert note is not None and "АДРЕС УСТАРЕЛ: core/confidence_gate.py" in note


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def test_memory_read_marks_the_line_and_counts_it(ws: Path) -> None:
    host = AgentLoopMemoryRead()
    host.log = _Log()
    host._file_read_workspace_root = lambda: ws
    block = "procedures:\n- [p1] edit core/alive.py::run\n- [p2] edit core/split_away.py"
    out = host._code_checked(block, "experience")
    lines = out.split("\n")
    assert lines[:2] == ["procedures:", "- [p1] edit core/alive.py::run"]
    assert "АДРЕС УСТАРЕЛ" in lines[2]
    assert host.log.events == [("stale_code_citation", {"source": "experience", "lines": 1})]
