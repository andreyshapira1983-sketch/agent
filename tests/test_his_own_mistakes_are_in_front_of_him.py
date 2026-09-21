"""Свои открытые записи о промахах стоят перед агентом в момент действия.

2026-09-21. Агент записал 20.09 в 22:00 «Explained a wall instead of testing
it — three refusals narrated instead of switching tools; the fix is to attempt
the action first». Через десять часов он трижды запросил строки 361, 481, 601
файла из 287 строк и объявил чтение невозможным. Реестр читался только при
ВЫБОРЕ работы; когда агент планировал шаги и писал «не могу», запись лежала в
ящике.

Проверяется: (1) блок строится из записей КАК ОНИ НАПИСАНЫ — запись без
`fingerprint` не пропадает, лекарство из `description`/`proposed_action` не
теряется, закрытая запись не показывается; (2) на настоящем ходе блок доходит
и до планировщика, и до синтезатора — того, кто пишет «не могу».
"""
from __future__ import annotations

from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.self_defect_reminder import (
    REGISTRY_RELPATH,
    format_open_self_defects,
    open_self_defects,
)
from core.state_integrity import append_state_jsonl_unlocked
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_WALL = {
    "title": "Explained a wall instead of testing it",
    "created_at": "2026-09-20T22:00:00Z", "status": "open", "severity": "medium",
    "fingerprint": "sii_wall",
    "description": "three refusals narrated instead of switching tools",
    "proposed_action": "attempt the action first and report the measured result",
}
_PROBE_NO_FINGERPRINT = {
    "id": "probe-cwd-isolation-2026-09-20", "status": "open",
    "title": "python_probe runs in a temp cwd, not the workspace",
    "why_it_matters": "self-experiments about my own files were measured against an empty directory",
}
_CLOSED = {"title": "старая закрытая запись", "status": "resolved", "fingerprint": "sii_old"}


def _registry(workspace: Path, *rows: dict) -> Path:
    path = workspace / REGISTRY_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    append_state_jsonl_unlocked(path, list(rows))
    return workspace


def test_the_block_carries_the_records_as_he_wrote_them(tmp_path) -> None:
    ws = _registry(tmp_path, _WALL, _PROBE_NO_FINGERPRINT, _CLOSED)
    block = format_open_self_defects(open_self_defects(ws))
    assert "Explained a wall instead of testing it" in block
    assert "attempt the action first" in block, "лекарство не должно теряться"
    assert "python_probe runs in a temp cwd" in block, "запись без fingerprint не пропадает"
    assert "старая закрытая запись" not in block


def test_no_registry_no_block(tmp_path) -> None:
    assert format_open_self_defects(open_self_defects(tmp_path)) == ""


def _answer() -> str:
    return ("Conclusion:\nготово [general-knowledge]\n"
            "Facts:\n- факт [general-knowledge]\n"
            "Sources:\n1. [general-knowledge]\n"
            "Confidence: medium\nUnverified:\nnothing\nSafety:\nnothing\n")


def test_the_planner_and_the_synthesizer_both_see_it(tmp_path) -> None:
    ws = _registry(tmp_path, _WALL, _PROBE_NO_FINGERPRINT)
    planner_llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    synth_llm = FakeLLM(responses=[_answer()])
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=ws))
    loop = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=ws / "logs", verbose=False),
    )

    def routed(role, task, *, escalation=None, task_role=None):
        key = role.value if hasattr(role, "value") else str(role)
        return planner_llm if key == "planner" else synth_llm

    loop.model_router.for_task = routed  # type: ignore[method-assign]
    loop.run("прочитай начало файла tools/python_probe.py")

    planner_text = " ".join(c["system"] + c["user"] for c in planner_llm.calls)
    synth_text = " ".join(c["system"] + c["user"] for c in synth_llm.calls)
    assert "<open_self_defects>" in planner_text
    assert "Explained a wall instead of testing it" in planner_text
    assert "<open_self_defects>" in synth_text, "синтезатор — тот, кто пишет «не могу»"
    assert "attempt the action first" in synth_text
