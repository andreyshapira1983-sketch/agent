"""Model router tests.

The router is the boundary that lets the agent core swap/compare models by
role instead of binding the whole runtime to one backend.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.model_router import ModelRole, ModelRoute, ModelRouter
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def _proposal_json() -> str:
    return json.dumps({
        "diagnosis": "answer() returns 41 while the failing test expects 42",
        "target_file": "buggy.py",
        "proposed_content": "def answer():\n    return 42\n",
        "evidence": ["tests/test_buggy.py::test_answer failed"],
        "confidence": 0.82,
    })


def _seed_repair_workspace(workspace: Path) -> None:
    (workspace / "tests").mkdir()
    (workspace / "buggy.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    (workspace / "tests" / "test_buggy.py").write_text(
        "from buggy import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )


def _fake_failing_pytest(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        class C:
            returncode = 1
            stdout = b"1 failed in 0.01s\nFAILED tests/test_buggy.py::test_answer - AssertionError\n"
            stderr = b""

        return C()

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_single_router_preserves_legacy_one_llm_behavior():
    llm = FakeLLM()
    router = ModelRouter.single(llm)

    assert router.for_role(ModelRole.PLANNER) is llm
    assert router.for_role(ModelRole.SYNTHESIZER) is llm
    assert router.for_role(ModelRole.REPAIR_PROPOSAL) is llm
    assert router.routing_summary()[ModelRole.PLANNER.value]["provider"] == "fake"


def test_router_reads_default_and_role_specific_env(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_MODEL", "base-model")
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "planner-model")
    monkeypatch.setenv("AGENT_REPAIR_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_REPAIR_MODEL", "repair-model")

    created: list[tuple[str | None, str | None]] = []

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        created.append((provider, model))
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)

    planner = router.for_role(ModelRole.PLANNER)
    synth = router.for_role(ModelRole.SYNTHESIZER)
    repair = router.for_role(ModelRole.REPAIR_PROPOSAL)

    assert (planner.provider, planner.model) == ("mock", "planner-model")
    assert (synth.provider, synth.model) == ("mock", "base-model")
    assert (repair.provider, repair.model) == ("openai", "repair-model")
    assert created == [
        ("mock", "planner-model"),
        ("mock", "base-model"),
        ("openai", "repair-model"),
    ]


def test_router_reuses_one_model_instance_for_identical_routes():
    created = 0

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        nonlocal created
        created += 1
        llm = FakeLLM()
        llm.provider = provider or "mock"
        llm.model = model or "same"
        return llm

    router = ModelRouter(
        default_provider="mock",
        default_model="same",
        llm_factory=factory,
    )

    assert router.for_role(ModelRole.PLANNER) is router.for_role(ModelRole.SYNTHESIZER)
    assert router.for_role(ModelRole.REPAIR_PROPOSAL) is router.for_role(ModelRole.PLANNER)
    assert created == 1


def test_agent_loop_uses_repair_proposal_route(tmp_path: Path, monkeypatch):
    _seed_repair_workspace(tmp_path)
    _fake_failing_pytest(monkeypatch)

    synth_llm = FakeLLM(responses=["synth should not be called"])
    repair_llm = FakeLLM(responses=[_proposal_json()])
    planner_llm = FakeLLM(responses=["planner should not be called"])

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        if model == "repair":
            return repair_llm
        if model == "planner":
            return planner_llm
        return synth_llm

    router = ModelRouter(
        default_provider="fake",
        default_model="synth",
        routes={
            ModelRole.PLANNER: ModelRoute(
                role=ModelRole.PLANNER.value,
                provider="fake",
                model="planner",
            ),
            ModelRole.REPAIR_PROPOSAL: ModelRoute(
                role=ModelRole.REPAIR_PROPOSAL.value,
                provider="fake",
                model="repair",
            ),
        },
        llm_factory=factory,
    )

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    registry.register(FileWriteTool(workspace_root=tmp_path))
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=synth_llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs", verbose=False),
        planner=FakePlanner([]),
        model_router=router,
        approval_provider=AutoApprover(),
        max_replan_attempts=1,
    )

    report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    assert report.ok
    assert len(repair_llm.calls) == 1
    assert synth_llm.calls == []
    assert planner_llm.calls == []

