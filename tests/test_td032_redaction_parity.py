"""TD-032 slice 2 — redaction boundary on autonomous runtime / self-build paths."""
from __future__ import annotations

import json
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
from core.self_build_producer import _builder_generate, _llm_json
from core.source_registry_store import SourceRegistryStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_PII_EMAIL = "client-secret@example.com"


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


def test_builder_redacts_secret_in_file_content_before_llm() -> None:
    payload = {
        "content": "API_TOKEN = 'safe'\n",
        "test_paths": ["tests"],
        "confidence": 0.9,
        "reason": "redact test",
    }
    llm = FakeLLM(responses=[json.dumps(payload)])
    file_body = f"API_TOKEN = '{_AWS_KEY}'\n"
    out = _builder_generate(llm, "sample.py", file_body, "cleanup secrets")
    assert out.decision == "built"
    assert llm.calls
    user = llm.calls[0]["user"]
    assert _AWS_KEY not in user
    assert "[REDACTED:" in user


def test_llm_json_redacts_pii_in_user_before_llm() -> None:
    llm = FakeLLM(responses=['{"target": null, "diagnosis": "none"}'])
    parsed = _llm_json(
        llm,
        system="Pick a target.",
        user=f"Contact lead at {_PII_EMAIL} for approval.",
    )
    assert parsed is not None
    assert llm.calls
    user = llm.calls[0]["user"]
    assert _PII_EMAIL not in user
    assert "[REDACTED:pii-email]" in user


def _persisted_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_approval_inbox_redacts_secret_in_summary_on_disk(workspace: Path) -> None:
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    item = inbox.add(
        operation="proposed_task",
        summary=f"Rotate client key {_AWS_KEY} in staging",
    )

    assert _AWS_KEY not in item.summary
    assert "[REDACTED:" in item.summary
    assert _AWS_KEY not in _persisted_text(path)

    reloaded = ApprovalInbox(path=path)
    assert _AWS_KEY not in reloaded.get(item.id).summary  # type: ignore[union-attr]
    snap = reloaded.snapshot()
    assert _AWS_KEY not in json.dumps(snap)
    assert "[REDACTED:" in snap["items"][0]["summary"]


def test_approval_inbox_redacts_pii_in_reasons_on_disk(workspace: Path) -> None:
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    item = inbox.add(
        operation="proposed_task",
        summary="Review client contact",
        reasons=(f"Notify {_PII_EMAIL} after deploy",),
    )

    assert _PII_EMAIL not in item.reasons[0]
    assert "[REDACTED:pii-email]" in item.reasons[0]
    assert _PII_EMAIL not in _persisted_text(path)

    reloaded = ApprovalInbox(path=path)
    assert _PII_EMAIL not in reloaded.get(item.id).reasons[0]  # type: ignore[union-attr]
    assert "[REDACTED:pii-email]" in reloaded.snapshot()["items"][0]["reasons"][0]


def test_approval_inbox_redacts_secret_in_nested_payload_on_disk(
    workspace: Path,
) -> None:
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    item = inbox.add(
        operation="self_apply_lane.run",
        summary="Apply patch",
        payload={
            "nested": {
                "api_key": _AWS_KEY,
                "contact": _PII_EMAIL,
            }
        },
    )

    assert _AWS_KEY not in json.dumps(item.payload)
    assert _PII_EMAIL not in json.dumps(item.payload)
    assert "[REDACTED:" in json.dumps(item.payload)
    assert _AWS_KEY not in _persisted_text(path)
    assert _PII_EMAIL not in _persisted_text(path)

    reloaded = ApprovalInbox(path=path)
    stored = reloaded.get(item.id).payload  # type: ignore[union-attr]
    assert _AWS_KEY not in json.dumps(stored)
    assert _PII_EMAIL not in json.dumps(stored)
    snap_payload = reloaded.snapshot()["items"][0]["payload"]
    assert _AWS_KEY not in json.dumps(snap_payload)
    assert _PII_EMAIL not in json.dumps(snap_payload)
