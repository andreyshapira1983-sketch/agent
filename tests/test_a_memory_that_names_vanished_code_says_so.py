"""Запись памяти, называющая исчезнувший код, доходит до агента с пометкой.

Замер 2026-09-25 на сервере: 36 ссылок памяти на файлы, которых больше нет
(раскол, переименование, откат). Сверка — в момент чтения (core/code_citations.py,
по arXiv 2609.25130: «держится ли это утверждение», хранимое не уничтожается).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.code_citations import stale_refs
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
