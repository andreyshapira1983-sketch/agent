"""The own-decisions journal takes only decisions grounded in an existing file (records from the live run 26.09)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.journal_append import JournalAppendTool

PATH = "data/own_decisions.jsonl"
EVIDENCE = ("logs/campaign_24h_stderr.log", "data/notes/20260926T_morin_hash_tables.md",
            "knowledge_library/cs/txt/Morin_OpenDataStructures_Python.txt", "data/self_improvement_issues.jsonl")

HOLLOW = [
    (("решение будет сформулировано по собранным уликам: либо конкретный следующий шаг по коду/источнику, "
      "либо 'спрашиваю человека' с названным случаем"),
     "улики: data/self_improvement_issues.jsonl, knowledge_library/cs/txt/Morin_OpenDataStructures_Python.txt"),
    ('"""Did this cycle incur an obligation to observe or act, and leave it unmet?', "data/self_improvement_issues.jsonl"),
    ("PLACEHOLDER", "PLACEHOLDER"),
    ("сначала замеряю частоту сигнала по журналам, затем читаю core/completion_obligation.py и решаю",
     "data/self_improvement_issues.jsonl: запись Investigate recurring detector signal"),
]
REAL = [
    (("Morin-цель закрыта: verdict=verified, конспект создан — повторять её не буду; по дефекту читаю код "
      "детектора и решаю по коду"),
     ("logs/campaign_24h_stderr.log:410 (verdict=verified); "
      "data/notes/20260926T_morin_hash_tables.md")),
    ("раздел найден замером (Theorem 5.1 — строка 3746); конспект записан; цель закрываю как выполненную",
     "knowledge_library/cs/txt/Morin_OpenDataStructures_Python.txt:3746 (Theorem 5.1)"),
]


@pytest.fixture
def tool(tmp_path: Path) -> JournalAppendTool:
    for rel in EVIDENCE:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", encoding="utf-8")
    return JournalAppendTool(workspace_root=tmp_path)


@pytest.mark.parametrize(("decision", "because"), HOLLOW)
def test_a_hollow_record_is_refused(tool: JournalAppendTool, decision: str, because: str) -> None:
    with pytest.raises(ValueError):
        tool.run(path=PATH, record={"id": "reshenie-ae8566b2", "about": "x", "decision": decision,
                                    "because": because})


@pytest.mark.parametrize(("decision", "because"), REAL)
def test_a_real_decision_is_kept(tool: JournalAppendTool, decision: str, because: str) -> None:
    out = tool.run(path=PATH, record={"id": "reshenie-ae8566b2", "about": "x", "decision": decision,
                                      "because": because})
    assert out["appended"] is True


def test_evidence_that_does_not_exist_is_not_evidence(tool: JournalAppendTool) -> None:
    with pytest.raises(ValueError, match="существующего файла"):
        tool.run(path=PATH, record={"id": "r", "about": "x", "decision": "правлю детектор",
                                    "because": "core/goal_guard.py:12 показывает сравнение по слову"})
