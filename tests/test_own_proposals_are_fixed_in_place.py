"""Свои предложения агент правит на месте; байты — как на диске; обход — не путь.

Разговор 2026-09-21 (вечер, трансляция): перезапись своего только что
записанного файла требовала одобрения человека, и правки шли новыми файлами —
proposals/ выросла до 17 версий одного документа. Оператор разрешил агенту
перезаписывать свои файлы в proposals/. Там же: доклад «17713 байт» при 18105
на диске (на Windows перевод строки — два байта), и первая мысль агента, как
обойти запрет, — «записать во временный файл и одним шагом заменить старый».
"""
from __future__ import annotations

from core.planner_prompt import PLANNER_SYSTEM
from tools.file_write import FileWriteTool


def test_overwriting_an_own_proposal_is_reversible(tmp_path) -> None:
    tool = FileWriteTool(workspace_root=tmp_path)
    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "p.md").write_text("old", encoding="utf-8")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "x.py").write_text("x = 1\n", encoding="utf-8")
    assert tool.risk_for({"path": "proposals/p.md"}) == "reversible"
    assert tool.risk_for({"path": "core/x.py"}) == "irreversible"
    assert tool.risk_for({"path": "proposals/new.md"}) == "reversible"


def test_bytes_written_are_the_bytes_on_disk(tmp_path) -> None:
    tool = FileWriteTool(workspace_root=tmp_path)
    out = tool.run(path="notes.md", content="строка один\nстрока два\n")
    assert out["bytes_written"] == (tmp_path / "notes.md").stat().st_size


def test_the_planner_is_told_a_refusal_is_not_an_obstacle() -> None:
    assert "temp file + rename" in PLANNER_SYSTEM
    assert "HUMAN decision, not an\n    obstacle" in PLANNER_SYSTEM
