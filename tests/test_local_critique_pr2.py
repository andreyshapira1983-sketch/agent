"""Local-critique path when ReferentResolver is on (critique plan PR2)."""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_CRITIQUE_ANSWER = """\
Conclusion:
В тексте есть категоричные выводы без доказательств. [prior_turn:PLACEHOLDER]
Facts:
- Утверждение смешивает инструкцию и факт. [prior_turn:PLACEHOLDER]
Sources:
1. prior_turn - prior turn
Confidence: medium
Unverified:
nothing
Safety:
nothing
"""

_OBJECT_MISSING_PHRASES = (
    "объект не указан",
    "без самого объекта",
    "не предоставлен",
    "object not specified",
    "no object",
)


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _agent(workspace: Path, *, llm: FakeLLM | None = None) -> tuple[AgentLoop, Path, FakePlanner]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    planner = FakePlanner(
        [
            {
                "tool": "file_read",
                "arguments": {"path": "should_not_run.txt"},
                "label": "file:should_not_run.txt",
                "expected_outcome": "unused",
            }
        ]
    )
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm
        or FakeLLM(
            responses=[
                "Conclusion: ok\nFacts:\n- x [user:target]\nSources: user:target\n"
                "Confidence: low\nUnverified: -\nSafety: ok"
            ]
        ),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        memory=WorkingMemory(),
        max_replan_attempts=1,
    )
    return agent, log_path, planner


def test_local_critique_on_skips_planner_and_injects_target(
    workspace: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "on")
    llm = FakeLLM(responses=[_CRITIQUE_ANSWER])
    agent, log_path, planner = _agent(workspace, llm=llm)
    assert agent.memory is not None
    agent.memory.record_turn(
        question="длинный план запуска",
        planner_reasoning="",
        tools_used=[],
        artifact_labels=[],
        answer=(
            "Категоричный план без источников: рынок вырастет на 400% за месяц, "
            "конкурентов нет, риски нулевые."
        ),
    )
    answer = agent.run("покажи слабые стороны этого")

    assert planner.calls == []  # forced skip — FakePlanner never invoked
    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    system = llm.calls[0]["system"]
    assert "<analysis_target" in user
    assert 'untrusted="true"' in user
    assert "<directive>" in user
    assert "Категоричный план" in user
    assert "general knowledge" not in user.lower()
    assert "<long_term_memory>" not in user
    assert "LOCAL CRITIQUE MODE" in system
    assert "[general-knowledge]" not in user or "Do not use [general-knowledge]" in user

    kinds = [e["event"] for e in _events(log_path)]
    assert "local_critique_path" in kinds
    assert "planner_local_critique" in kinds
    assert "referent_decision" in kinds
    ref = next(e for e in _events(log_path) if e["event"] == "referent_decision")
    assert ref["payload"]["local_critique_eligible"] is True
    assert ref["payload"]["would_change_answer"] is True
    assert ref["payload"]["mode"] == "on"

    low = answer.casefold()
    for phrase in _OBJECT_MISSING_PHRASES:
        assert phrase not in low


def test_local_critique_show_only_forbids_offer_language(
    workspace: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "on")
    llm = FakeLLM(
        responses=[
            "Conclusion: Слабые стороны перечислены. [user:target]\n"
            "Facts:\n- Тезис повторяется. [user:target]\n"
            "Sources:\n1. user:target\nConfidence: medium\n"
            "Unverified:\nnothing\nSafety:\nnothing\n"
        ]
    )
    agent, log_path, _planner = _agent(workspace, llm=llm)
    assert agent.memory is not None
    # Material + show-only in the same message (user_text kind).
    agent.run(
        "Вот фрагмент: рынок вырастет на 400 процентов без рисков и конкурентов. "
        "только покажи слабые стороны"
    )
    user = llm.calls[0]["user"]
    assert "Show-only:" in user or "show-only" in llm.calls[0]["system"].casefold()
    assert "<analysis_target" in user
    events = [e for e in _events(log_path) if e["event"] == "local_critique_path"]
    assert events
    assert events[0]["payload"]["show_only"] is True


def test_shadow_would_change_but_keeps_default_synth(
    workspace: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "shadow")
    llm = FakeLLM(
        responses=[
            "Conclusion: Нет объекта. [general-knowledge]\n"
            "Facts:\n- объект не указан [general-knowledge]\n"
            "Sources:\n1. general-knowledge\nConfidence: low\n"
            "Unverified:\nnothing\nSafety:\nnothing\n"
        ]
    )
    # Empty planner sources so FakePlanner is used if called.
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    planner = FakePlanner([])
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        memory=WorkingMemory(),
        max_replan_attempts=1,
    )
    assert agent.memory is not None
    agent.memory.record_turn(
        question="q",
        planner_reasoning="",
        tools_used=[],
        artifact_labels=[],
        answer="Предыдущий длинный ответ для критики слабых сторон текста.",
    )
    agent.run("покажи слабые стороны этого")
    ref = next(e for e in _events(log_path) if e["event"] == "referent_decision")
    assert ref["payload"]["would_change_answer"] is True
    assert ref["payload"]["mode"] == "shadow"
    # Shadow must NOT take the local-critique path.
    assert "local_critique_path" not in [e["event"] for e in _events(log_path)]
    user = llm.calls[0]["user"]
    assert "<analysis_target" not in user
    assert "general knowledge" in user.lower() or "[general-knowledge]" in user


def test_off_mode_unchanged(workspace: Path, monkeypatch):
    monkeypatch.delenv("AGENT_REFERENT_RESOLVER", raising=False)
    llm = FakeLLM(
        responses=[
            "Conclusion: ok\nFacts:\n- x [general-knowledge]\nSources: gk\n"
            "Confidence: low\nUnverified: -\nSafety: ok"
        ]
    )
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    planner = FakePlanner([])
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        memory=WorkingMemory(),
        max_replan_attempts=1,
    )
    assert agent.memory is not None
    agent.memory.record_turn(
        question="q",
        planner_reasoning="",
        tools_used=[],
        artifact_labels=[],
        answer="Материал для анализа слабых сторон.",
    )
    agent.run("покажи слабые стороны этого")
    assert "referent_decision" not in [e["event"] for e in _events(log_path)]
    assert "local_critique_path" not in [e["event"] for e in _events(log_path)]
    assert len(planner.calls) == 1
    assert "<analysis_target" not in llm.calls[0]["user"]
