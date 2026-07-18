"""Iteration 1 (LPF-001): host_tools must be injected as a non-citable
<host_environment> context block, NOT as <evidence source="host_tools">.

Injecting it as <evidence> flips the synthesizer into "answer STRICTLY from
evidence" mode (core/loop_helpers.py SYSTEM_ANSWER), which disables the
general-knowledge path and produces fabricated [tool:host_tools] citations that
the verifier can never match. See docs/LIVE_PROBE_FINDINGS.md (LPF-001).
"""
from __future__ import annotations

from pathlib import Path

from core.loop import AgentLoop, new_trace_id
from core.logger import TraceLogger
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


_CONTRACT_ANSWER = (
    "Conclusion:\nI can help within the available context. [general-knowledge]\n"
    "Facts:\n- general assistance [general-knowledge]\n"
    "Sources:\n1. [general-knowledge]\n"
    "Confidence: low\n"
    "Unverified:\n- none\n"
    "Safety:\n- none\n"
)


def _agent(workspace: Path, fake: FakeLLM) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    # Planner returns no tools -> the loop goes straight to synthesis with an
    # empty evidence chain (the exact condition that exposes the bug).
    planner = FakePlanner(sources=[])
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=fake,
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=planner,
    )


def test_host_tools_is_host_environment_not_evidence(workspace: Path, monkeypatch):
    # A configured host tool (existence not required — the block lists it either
    # way) so _build_host_tools_block returns a non-empty block.
    monkeypatch.setenv("BLENDER_PATH", str(workspace / "blender.exe"))

    fake = FakeLLM(responses=[_CONTRACT_ANSWER])
    agent = _agent(workspace, fake)
    agent.run("How much is 17 times 23?")

    # The synthesizer prompt(s) captured by the FakeLLM.
    synth_prompts = "\n".join(c["user"] for c in fake.calls)

    # The host block IS injected (the feature is preserved)...
    assert "blender" in synth_prompts.lower(), "host tools block should still be injected"
    # ...but NOT as a citable <evidence source="host_tools"> block (fails on old code)...
    assert '<evidence source="host_tools">' not in synth_prompts
    # ...and instead as a non-citable <host_environment> block (fails on old code).
    assert "<host_environment>" in synth_prompts
