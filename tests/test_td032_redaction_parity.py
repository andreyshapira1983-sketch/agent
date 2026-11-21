"""TD-032 slice 2 — redaction boundary on autonomous runtime / self-build paths."""
from __future__ import annotations

from pathlib import Path

from core.approval import AutoApprover
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
    AutonomousTask,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


class ProposeOnlyRuntime(AutonomousRuntime):
    def _build_queue(self, config: AutonomousRuntimeConfig) -> list[AutonomousTask]:
        return [AutonomousTask("propose", "test-only propose task")]


def _agent(workspace: Path, llm: FakeLLM) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )


def test_propose_redacts_secret_in_digest_before_llm(workspace: Path) -> None:
    llm = FakeLLM(responses=['{"proposals":[]}'])
    agent = _agent(workspace, llm)
    store = agent.persistent_store
    assert store is not None
    store.save(
        MemoryRecord(
            content=f"Client vault key is {_AWS_KEY} for staging.",
            tags=("lesson",),
        )
    )

    report = ProposeOnlyRuntime(
        agent, workspace=workspace, approval_inbox=ApprovalInbox()
    ).run(
        AutonomousRuntimeConfig(
            limit=1,
            include_tests=False,
            enable_reflection=False,
            include_proposals=True,
        )
    )

    propose = next(t for t in report.tasks if t.task.kind == "propose")
    assert propose.status != "failed"
    assert llm.calls
    user = llm.calls[0]["user"]
    assert _AWS_KEY not in user
    assert "[REDACTED:" in user
