"""Файл читается целиком одним вызовом и не перечитывается в том же ходе.

Эпизод 2026-09-21 08:53 (след trace_d3f977c1…): core/smart_memory.py (83 КБ)
прочитан тридцатью окнами по 60 строк; MEMORY_SYSTEM_AUDIT.md и
self-audit-lessons.md — по три раза за ход. Две причины, обе в том, что ему
говорили:
* инструкция планировщику: «чтение целого файла ОБРЕЗАЕТСЯ до ~12 000 знаков,
  файл больше ~300 строк никогда не приходит целиком» — после подъёма потолка
  это неправда, и она гнала листать окнами;
* круг наблюдения показывал только последний пакет — прочитанное раньше
  планировщик не видел и открывал заново.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from core.evidence_budget import EVIDENCE_FILE_CHARS
from core.observation_round import format_observations
from core.planner_prompt import PLANNER_SYSTEM


def _file_read_paragraph() -> str:
    start = PLANNER_SYSTEM.index("- file_read(")
    return PLANNER_SYSTEM[start:PLANNER_SYSTEM.index("\n- ", start + 1)]


def test_the_planner_is_told_the_real_ceiling() -> None:
    text = _file_read_paragraph()
    assert "TRUNCATED to a ~12 000" not in text
    said = re.search(r"~(\d[\d ]*) chars", text)
    assert said, text
    assert int(said.group(1).replace(" ", "")) == EVIDENCE_FILE_CHARS, (
        "инструкция и настоящий потолок разошлись — агент будет листать зря")
    assert "WHOLE in ONE call" in text
    assert "twice in one turn" in text


def test_an_earlier_round_is_named_to_the_next() -> None:
    plan = SimpleNamespace(steps=[])
    block = format_observations(
        plan, {"file:b.md": {"tool": "file_read", "output": "b"}},
        earlier=["file:a.md", "file:core/smart_memory.py"])
    line = next(ln for ln in block.splitlines() if ln.startswith("Already read"))
    assert "file:a.md" in line and "file:core/smart_memory.py" in line
    assert "do NOT read again" in line


def test_the_first_round_has_nothing_earlier() -> None:
    block = format_observations(SimpleNamespace(steps=[]), {})
    assert "Already read" not in block
