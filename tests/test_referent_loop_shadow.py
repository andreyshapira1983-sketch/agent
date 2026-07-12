"""Shadow wiring for ReferentResolver in AgentLoop (critique PR1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _agent(workspace: Path) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=["Conclusion: ok\nFacts:\n- x [general-knowledge]\nSources: gk\nConfidence: low\nUnverified: -\nSafety: ok"]),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner([]),
        approval_provider=AutoApprover(default="approve"),
        memory=WorkingMemory(),
        max_replan_attempts=1,
    )
    return agent, log_path


def test_referent_shadow_off_emits_no_event(workspace: Path, monkeypatch):
    monkeypatch.delenv("AGENT_REFERENT_RESOLVER", raising=False)
    agent, log_path = _agent(workspace)
    agent.run("покажи слабые стороны этого")
    kinds = [e["event"] for e in _events(log_path)]
    assert "referent_decision" not in kinds
    assert agent.last_referent_decision is None


def test_referent_shadow_logs_decision(workspace: Path, monkeypatch):
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "shadow")
    agent, log_path = _agent(workspace)
    mem = agent.memory
    assert mem is not None
    mem.record_turn(
        question="длинная формулировка",
        planner_reasoning="",
        tools_used=[],
        artifact_labels=[],
        answer="Предыдущий ответ для критики слабых сторон.",
    )
    agent.run("покажи слабые стороны этого")
    events = [e for e in _events(log_path) if e["event"] == "referent_decision"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["mode"] == "shadow"
    assert payload["would_change_answer"] is False
    assert payload["status"] in {"resolved", "ambiguous", "unresolved", "needs_tool"}
    assert agent.last_referent_decision is not None
