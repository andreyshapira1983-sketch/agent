"""Regression coverage for durable learning state in autonomous dry-runs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.assumption_registry import Assumption, AssumptionStore
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_echo_antibody import MemoryWriteRegistry
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.policy import PolicyGate
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
)
from core.source_registry_store import SourceRegistryStore
from core.user_profile import UserProfile, UserProfileStore
from core.work_session import WorkSessionConfig, run_work_session
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


_DURABLE_STATE_FILES = (
    "persistent_memory.jsonl",
    "memory_writes.jsonl",
    "episodic_memory.jsonl",
    "procedural_memory.jsonl",
    "memory_consolidation.jsonl",
    "source_registry.jsonl",
    "user_profile.jsonl",
    "assumptions.jsonl",
)

_GOAL = "Read doc.txt with Python 3.12 and summarize dry-run persistence."
_SYNTHESIS = (
    "Conclusion: The fixture documents dry-run persistence. [file:doc.txt]\n"
    "Facts:\n- dry-run persistence is documented [file:doc.txt]\n"
    "Sources:\n1. file:doc.txt - doc.txt\n"
    "Confidence: high\nUnverified: nothing\nSafety: nothing\n"
)
_LESSONS_JSON = (
    '[{"insight": "a repeated timeout needs bounded retry", '
    '"action": "monitor", "focus_area": "core/runtime.py", "confidence": 0.8}]'
)


def _state_snapshot(workspace: Path) -> dict[str, bytes | None]:
    data = workspace / "data"
    return {
        name: (data / name).read_bytes() if (data / name).exists() else None
        for name in _DURABLE_STATE_FILES
    }


def _seed_reflection_logs(workspace: Path) -> None:
    events = [
        {
            "trace_id": "seed-1",
            "event": "tool_call",
            "payload": {"id": "call-1", "tool_name": "web_fetch"},
        },
        {
            "trace_id": "seed-1",
            "event": "tool_result",
            "payload": {
                "tool_call_id": "call-1",
                "status": "error",
                "error": "timeout",
            },
        },
        {
            "trace_id": "seed-2",
            "event": "tool_call",
            "payload": {"id": "call-2", "tool_name": "web_fetch"},
        },
        {
            "trace_id": "seed-2",
            "event": "tool_result",
            "payload": {
                "tool_call_id": "call-2",
                "status": "error",
                "error": "timeout",
            },
        },
    ]
    log_dir = workspace / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "reflection_seed.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )


def _durable_agent(workspace: Path) -> AgentLoop:
    (workspace / "doc.txt").write_text(
        "Dry-run must not grow durable learning state.",
        encoding="utf-8",
    )
    _seed_reflection_logs(workspace)

    data = workspace / "data"
    persistent_store = PersistentMemoryStore(data / "persistent_memory.jsonl")
    persistent_store.save(
        MemoryRecord(
            type="semantic",
            content="Read doc.txt with Python 3.12 and summarize dry-run persistence.",
            tags=["dry-run", "persistence"],
            owner="user",
        )
    )
    source_store = SourceRegistryStore(data / "source_registry.jsonl")
    episodic_store = EpisodicMemoryStore(data / "episodic_memory.jsonl")
    procedural_store = ProceduralMemoryStore(data / "procedural_memory.jsonl")
    consolidation_store = MemoryConsolidationStore(
        data / "memory_consolidation.jsonl"
    )
    profile_store = UserProfileStore(data / "user_profile.jsonl")
    profile_store.save(UserProfile(language="en", interaction_count=7))
    assumption_store = AssumptionStore(data / "assumptions.jsonl")
    assumption_store.save(Assumption(text="seed assumption", run_id="seed"))
    memory_write_registry = MemoryWriteRegistry(data / "memory_writes.jsonl")

    for name in _DURABLE_STATE_FILES:
        (data / name).touch(exist_ok=True)

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    llm = FakeLLM(responses=[_SYNTHESIS, _LESSONS_JSON])
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=FakePlanner(
            sources=[
                {
                    "tool": "file_read",
                    "arguments": {"path": "doc.txt"},
                    "rationale": "read the fixture",
                }
            ]
        ),
        memory=WorkingMemory(),
        persistent_store=persistent_store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        memory_write_registry=memory_write_registry,
        source_registry_store=source_store,
        episodic_store=episodic_store,
        procedural_store=procedural_store,
        consolidation_store=consolidation_store,
        knowledge_auto_write=True,
        user_profile_store=profile_store,
        assumption_store=assumption_store,
        approval_provider=AutoApprover(default="approve"),
        verifier_enabled=False,
        max_replan_attempts=1,
    )


@pytest.mark.parametrize("entrypoint", ["auto_run", "work_session"])
def test_dry_run_preserves_all_durable_learning_state(
    workspace: Path, entrypoint: str
) -> None:
    agent = _durable_agent(workspace)
    before = _state_snapshot(workspace)

    if entrypoint == "auto_run":
        report = AutonomousRuntime(agent, workspace=workspace).run(
            AutonomousRuntimeConfig(
                goal=_GOAL,
                dry_run=True,
                limit=3,
                include_tests=False,
                include_goal=True,
                enable_self_build=False,
            )
        )
        assert report.status == "completed"
        assert report.reflection is not None
        assert report.reflection["memory_records_saved"] == 0
    else:
        result = run_work_session(
            WorkSessionConfig(
                goal=_GOAL,
                dry_run=True,
                minutes=1.0,
                max_cycles=1,
            ),
            agent=agent,
            workspace=workspace,
        )
        assert result.status == "completed"

    assert _state_snapshot(workspace) == before
    assert agent.procedural_store.load() == []
    assert agent.source_registry_store.count() == {"sources": 0, "claims": 0}
    assert agent.persistent_store.load()[0].access_count == 0
    assert agent.suppress_durable_learning_writes is False
