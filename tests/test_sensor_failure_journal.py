"""MIR-077, класс 1 — сбой наблюдательного сенсора виден в журнале.

Ревью-раунды #283 и #286 поймали в свежем коде один и тот же дефект: широкий
`except` глотает сбой без события, и поломка подсистемы невидима. Карта
(#292) показала, что в `core/loop.py` так стояло 15 мест — половина всей
опасной массы, причём на пути ответа.

Одиннадцать из них — наблюдательные сенсоры (вектор уверенности, поддержка
доказательствами, разногласия подсистем, тень стагнации…): их сбой не должен
ронять ход, но обязан быть виден. Один помощник `_sensor_failed` вместо
одиннадцати копий try/except/log: место вызова остаётся двумя строками,
`core/loop.py` не растёт, а оператор получает ОДИН канал `sensor_failed`.

Остальные четыре — законные значения по умолчанию (откат на обычную модель,
отсутствие инструмента в реестре, недоступный реестр промптов): там причина
названа комментарием, и сканер аудита их больше не считает немотивированными.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(p: Path) -> list[dict]:
    out: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def _agent(workspace: Path, response: str):
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=[response]),
        logger=logger,
        planner=FakePlanner([]),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=True,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ── сам помощник ─────────────────────────────────────────────────────────────

def test_helper_journals_sensor_name_and_error(workspace: Path):
    agent, log_path = _agent(workspace, "ответ")
    agent._sensor_failed("confidence_vector", RuntimeError("сенсор сломан"))
    events = [e for e in _events(log_path) if e.get("event") == "sensor_failed"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["sensor"] == "confidence_vector"
    assert payload["error_type"] == "RuntimeError"
    assert "сенсор сломан" in payload["error"]


def test_helper_survives_a_broken_logger(workspace: Path):
    """Последний рубеж: если журналирование само упало, ход всё равно жив."""
    agent, _ = _agent(workspace, "ответ")

    class _BrokenLog:
        def log(self, *_a, **_k):
            raise OSError("диск полон")

    agent.log = _BrokenLog()
    agent._sensor_failed("evidence_support", ValueError("x"))  # не должно бросить


# ── сквозь реальный цикл ─────────────────────────────────────────────────────

def test_broken_confidence_vector_is_visible_and_turn_survives(
    workspace: Path, monkeypatch
):
    """Сломанный сенсор больше не исчезает бесследно: ответ выдан, сбой в журнале."""
    import core.confidence_vector as cv

    def _boom(**_kwargs):
        raise RuntimeError("вектор уверенности сломан")

    monkeypatch.setattr(cv, "compute_vector", _boom)
    agent, log_path = _agent(
        workspace, "Conclusion: обычный ответ [general-knowledge]."
    )
    answer = agent.run("вопрос без инструментов")
    assert answer, "ход обязан завершиться несмотря на сломанный сенсор"
    failures = [
        e for e in _events(log_path)
        if e.get("event") == "sensor_failed"
        and e["payload"]["sensor"] == "confidence_vector"
    ]
    assert len(failures) == 1
    assert failures[0]["payload"]["error_type"] == "RuntimeError"


def test_healthy_turn_reports_no_sensor_failure(workspace: Path):
    agent, log_path = _agent(
        workspace, "Conclusion: обычный ответ [general-knowledge]."
    )
    agent.run("вопрос без инструментов")
    assert not [e for e in _events(log_path) if e.get("event") == "sensor_failed"]


# ── карта аудита ─────────────────────────────────────────────────────────────

def test_loop_py_has_no_unjustified_silent_handlers_left():
    """Класс закрыт целиком по этому файлу — храповик опущен на все 15."""
    import importlib.util

    repo = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "except_audit", repo / "scripts" / "except_audit.py"
    )
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    bad = [
        r for r in audit.unjustified_silent(audit.audit())
        if r["file"] == "core/loop.py"
    ]
    assert bad == [], f"в loop.py остались немотивированные молчуны: {bad}"
