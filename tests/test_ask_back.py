"""MIR-075 — the agent asks back instead of only philosophising unsupported.

Operator assignment (2026-08-03): «он должен хотя бы понять, что у него
требуют, или переспросить — как мы видим, он НЕ переспрашивает, он сразу
начинает делать». Measured on the operator's live five-question test: the
clarification gate was structurally unreachable (its only production trigger
is replan exhaustion, and every answer succeeded on attempt 1), and the
completion contract's `needs_clarification` has zero consumers.

The trigger is deliberately NARROW and built from measured post-answer
numbers, not question wording (the lexical targetless rule was retired in
#263 after scoring 8 of 8 false): a turn already classified self-analysis
(`is_self_analysis_turn`, existing deterministic sensor) whose final answer
carries ZERO verified chunks — exactly the operator's turn 3 (5/5
self_declared, confidence 0.027) and none of his other four turns.

The ask-back rides the notice ledger (survives body rewrites) and the human
formatter keeps it by its fixed prefix — the same two lessons MIR-069 paid
for.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.clarification_gate import ASK_BACK_PREFIX, build_self_analysis_ask_back
from core.logger import TraceLogger
from core.loop import AgentLoop, format_human_response, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tests.conftest import FakeLLM, FakePlanner


# ── the pure builder ─────────────────────────────────────────────────────────

def test_ask_back_text_is_a_question_with_the_fixed_prefix():
    text = build_self_analysis_ask_back()
    assert text.startswith(ASK_BACK_PREFIX)
    assert "?" in text
    assert "конкрет" in text.lower(), "переспрос должен звать к сужению рамки"


def test_human_formatter_keeps_the_ask_back_line():
    """Same failure mode MIR-069 paid for: the Output Contract strip must
    bucket the fixed prefix explicitly or the operator never sees it."""
    answer = (
        "Conclusion: рассуждение о себе.\n"
        "Facts:\n- мысль без источника.\n"
        "Sources: general-knowledge\n"
        "Confidence: low\n"
        "\n"
        + build_self_analysis_ask_back()
    )
    human = format_human_response(answer)
    assert ASK_BACK_PREFIX in human


# ── wired through the real loop ──────────────────────────────────────────────

def _events(p: Path) -> list[dict]:
    out: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _agent(workspace: Path, responses: list[str], sources: list[dict]):
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    for src in sources:
        src.setdefault("expected_outcome", "executes the planned step")
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=responses),
        logger=logger,
        planner=FakePlanner(sources),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=True,
        memory=WorkingMemory(),
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


_SELF_Q = "что ты считаешь правильным, а что ты считаешь неправильным в себе?"


def test_unsupported_self_analysis_answer_asks_back(workspace: Path):
    agent, log_path = _agent(
        workspace,
        responses=[
            "Conclusion: первый ответ [general-knowledge].",
            "Conclusion: у меня нет мнений, я рассуждаю без источников.\n"
            "Facts:\n- это самоописание без опоры.",
        ],
        sources=[],
    )
    agent.run("расскажи о проекте")            # прошлый ход — есть история
    answer = agent.run(_SELF_Q)
    assert ASK_BACK_PREFIX in answer, "ноль подтверждённого на вопросе о себе — обязан переспросить"
    events = [e for e in _events(log_path) if e.get("event") == "clarification_ask_back"]
    assert len(events) == 1


def test_supported_self_analysis_answer_does_not_ask_back(workspace: Path):
    (workspace / "doc.txt").write_text("agent facts", encoding="utf-8")
    agent, _ = _agent(
        workspace,
        responses=[
            "Conclusion: первый ответ [general-knowledge].",
            "Conclusion: отвечаю по файлу [file:doc.txt].\n"
            "Facts:\n- факт из файла [file:doc.txt].",
        ],
        sources=[{
            "tool": "file_read", "arguments": {"path": "doc.txt"},
            "label": "file:doc.txt",
        }],
    )
    agent.run("расскажи о проекте")
    answer = agent.run(_SELF_Q)
    assert ASK_BACK_PREFIX not in answer, "есть подтверждённая опора — переспрос был бы шумом"


def test_ordinary_question_without_support_does_not_ask_back(workspace: Path):
    agent, _ = _agent(
        workspace,
        responses=[
            "Conclusion: первый ответ [general-knowledge].",
            "Conclusion: обычный ответ из общих знаний.",
        ],
        sources=[],
    )
    agent.run("расскажи о проекте")
    answer = agent.run("что такое искусственный интеллект?")
    assert ASK_BACK_PREFIX not in answer, "обычный вопрос — не самоанализ, переспрос не о чем"
