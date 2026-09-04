"""A fact the loop measured is evidence to the loop (exam 2026-09-04, turn 3).

Asked, with the roster finally in front of him, which model answers which
role, he wrote fifteen claims off `<model_roster>`. The verifier classed
every one «user_asserted» (no tool evidence backed them), the low-evidence
gate suppressed the whole answer — «подавлено 15 непроверенных утверждений»
— and the draft was not journaled anywhere. Pinned here:

  - the roster block enters the evidence chain as `sensor:model_roster`
    and is offered to the synthesizer among the allowed citations;
  - a claim citing `[sensor:model_roster]` whose words are in the block
    is VERIFIED, and the answer ships;
  - when the gate does suppress, the head of what it suppressed is journaled.
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

PLAN_EMPTY = json.dumps({"reasoning": "answer from the roster in context", "steps": []})
SYNTH = (
    "Conclusion: The planner role is answered by openai/gpt-5.6-sol, pinned in .env by the operator. [sensor:model_roster]\n"
    "Facts:\n- planner: openai/gpt-5.6-sol — env_pin [sensor:model_roster]\n"
    "Sources:\n1. sensor:model_roster - my own model roster\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _agent(tmp_path: Path, monkeypatch, llm):
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    monkeypatch.setenv("AGENT_PLANNER_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "gpt-5.6-sol")
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    router = ModelRouter(
        default_provider="openai", default_model="gpt-default",
        routes={ModelRole.PLANNER: ModelRoute(role="planner", provider="openai", model="gpt-5.6-sol", reason="env:AGENT_PLANNER")},
        llm_factory=lambda p, m: llm, usage_ledger=ledger,
    )
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    log_dir = tmp_path / "logs"
    logger = TraceLogger(trace_id=new_trace_id(), log_dir=log_dir, verbose=False)
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=llm, logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry), memory=None, model_router=router,
    )
    monkeypatch.setattr(agent, "_file_read_workspace_root", lambda: tmp_path, raising=False)
    return agent, log_dir


def _events(log_dir: Path) -> list[dict]:
    out = []
    for path in log_dir.glob("trace_*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def test_a_claim_read_off_the_roster_is_verified_and_the_answer_ships(tmp_path, monkeypatch):
    llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH])
    agent, log_dir = _agent(tmp_path, monkeypatch, llm)

    answer = agent.run(user_question="Какая модель отвечает за планировщика и кто это решил?")

    synth_user = [c for c in llm.calls if "PLANNER_MODE" not in c["system"]][-1]["user"]
    assert "[sensor:model_roster]" in synth_user, "the roster must be offered among the allowed citations"
    events = {e["event"]: e for e in _events(log_dir) if "event" in e}
    verification = events["verification"]["payload"]
    assert verification["verified_chunks"] >= 1, verification
    assert "gpt-5.6-sol" in answer, answer
    assert "подавлено" not in answer and "Insufficient evidence" not in answer


def test_the_head_of_a_suppressed_answer_is_journaled():
    from core.low_evidence_policy import LowEvidencePolicyResult

    result = LowEvidencePolicyResult(
        triggered=True, answer="short", verified_chunks=0, total_chunks=15,
        verified_ratio=0.0, unverified_total=0, reason="r", suppressed_chars=900,
        suppressed_head="- planner: openai/gpt-5.6-sol …",
    )
    assert result.to_log_payload()["suppressed_head"].startswith("- planner")
