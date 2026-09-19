"""The eye reaches the answerer, not only the planner (exam 2026-09-04, turn 2).

Turn 1: asked which models he has and who assigned them, he searched the
web and answered «the materials do not allow…» while his own planner had
written that the model_roster block «should remain the main source».
Turn 2, told so, he answered: «the model_roster block is not in the
context passed to me now» — and that was TRUE: the spend mirror (which
carries the roster) went into the planner's history and never into the
synthesizer's prompt. Declared built, half wired. Pinned here: the block
the planner sees, the synthesizer sees too.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.model_router import ModelRole, ModelRoute, ModelRouter
from core.model_usage import ModelUsageLedger
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

PLAN_EMPTY = json.dumps({"reasoning": "answer from context", "steps": []})
SYNTH = (
    "Conclusion: planner runs on openai/gpt-5.6-sol by the operator's pin.\n"
    "Facts:\n- see roster\nSources:\n1. context\nConfidence: medium\nUnverified: nothing\n"
)


def test_the_roster_block_is_in_the_synthesizer_prompt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    monkeypatch.setenv("AGENT_PLANNER_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "gpt-5.6-sol")
    llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH])
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    router = ModelRouter(
        default_provider="openai", default_model="gpt-default",
        routes={ModelRole.PLANNER: ModelRoute(role="planner", provider="openai", model="gpt-5.6-sol", reason="env:AGENT_PLANNER")},
        llm_factory=lambda p, m: llm, usage_ledger=ledger,
    )
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    logger = TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=llm, logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry), memory=None, model_router=router,
    )
    monkeypatch.setattr(agent, "_file_read_workspace_root", lambda: tmp_path, raising=False)

    agent.run(user_question="Какая модель отвечает за планировщика и кто это решил?")

    planner_calls = [c for c in llm.calls if "PLANNER_MODE" in c["system"]]
    synth_calls = [c for c in llm.calls if "PLANNER_MODE" not in c["system"]]
    assert planner_calls and synth_calls
    assert "<model_roster>" in planner_calls[0]["user"], "the planner saw the eye before this fix too"
    assert "<model_roster>" in synth_calls[-1]["user"], "the answerer must see what the planner saw"
    assert "planner: openai/gpt-5.6-sol" in synth_calls[-1]["user"]
