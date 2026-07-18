"""host_tools context injection (LPF-001 iterations 1 + 1b).

Iteration 1 (Option B): host_tools is a non-citable <host_environment> block, NOT
<evidence source="host_tools"> (which used to disable the general-knowledge path
and produce fabricated [tool:host_tools] citations).

Iteration 1b (Option A): the <host_environment> block is injected ONLY when the
turn is actually about host tools (name / task word / an effect tool was used),
so `.env` paths no longer ride along on unrelated questions.

See docs/LIVE_PROBE_FINDINGS.md (LPF-001).
"""
from __future__ import annotations

from pathlib import Path

from core.loop import AgentLoop, new_trace_id
from core.logger import TraceLogger
from core.planner import host_tools_relevant
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


_CONTRACT_ANSWER = (
    "Conclusion:\nHere is the answer. [general-knowledge]\n"
    "Facts:\n- a fact [general-knowledge]\n"
    "Sources:\n1. [general-knowledge]\n"
    "Confidence: low\n"
    "Unverified:\n- none\n"
    "Safety:\n- none\n"
)


def _agent(workspace: Path, fake: FakeLLM) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    planner = FakePlanner(sources=[])  # no tools -> straight to synthesis
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=fake,
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=planner,
    )


def _synth_prompts(fake: FakeLLM) -> str:
    return "\n".join(c["user"] for c in fake.calls)


# ── Iteration 1 (Option B): host block is <host_environment>, never <evidence> ──

def test_host_tools_is_host_environment_not_evidence(workspace: Path, monkeypatch):
    monkeypatch.setenv("BLENDER_PATH", str(workspace / "blender.exe"))
    fake = FakeLLM(responses=[_CONTRACT_ANSWER])
    agent = _agent(workspace, fake)
    # A host-relevant question so the block IS injected.
    agent.run("Write a Blender script to make a cube")

    prompts = _synth_prompts(fake)
    assert "<host_environment>" in prompts
    assert '<evidence source="host_tools">' not in prompts


# ── Iteration 1b (Option A): block is NOT injected on unrelated questions ──────

def test_host_environment_not_injected_when_irrelevant(workspace: Path, monkeypatch):
    monkeypatch.setenv("BLENDER_PATH", str(workspace / "blender.exe"))
    fake = FakeLLM(responses=[_CONTRACT_ANSWER])
    agent = _agent(workspace, fake)
    # A self-contained arithmetic question — nothing to do with host tools.
    agent.run("How much is 17 times 23?")

    prompts = _synth_prompts(fake)
    assert "<host_environment>" not in prompts
    assert "blender" not in prompts.lower()


# ── Pure relevance-gate unit checks (deterministic, no LLM) ────────────────────

def test_host_tools_relevant_gate():
    # Relevant: explicit tool name / task word / effect tool used.
    assert host_tools_relevant("write a blender script") is True
    assert host_tools_relevant("can you make a Word document") is True
    assert host_tools_relevant("render a 3d model") is True
    assert host_tools_relevant("do this", tools_used=["shell_exec"]) is True
    assert host_tools_relevant("save it", tools_used=["file_write"]) is True
    # Irrelevant: no name, no task word, no effect tool.
    assert host_tools_relevant("How much is 17 times 23?") is False
    assert host_tools_relevant("Who are you?") is False
    assert host_tools_relevant("what is the capital of France") is False
    # No false-substring trigger ("word" inside "keyword" must not fire).
    assert host_tools_relevant("give me a keyword for SEO") is False
