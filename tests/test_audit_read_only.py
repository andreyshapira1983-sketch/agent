"""Regression coverage for the :audit read-only execution brake.

These lock in the deterministic "no durable memory write during an audit"
contract: engaging audit mode before a cycle must block EVERY durable learning
write (episodic, procedural, consolidation, user profile, persistent
access-stat bumps) and freeze agent-auto persistent/semantic writes, while the
operator's explicit :remember (source='user-explicit') keeps working. An audit
therefore cannot silently contaminate the memory it is investigating, and an
incomplete investigation cannot bank itself as a successful procedure.
"""
from __future__ import annotations

from pathlib import Path

from core.approval import AutoApprover
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
from core.user_profile import UserProfileStore
from main import handle_meta_command
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_GOAL = "Read doc.txt and summarize the audit fixture."
_SYNTHESIS = (
    "Conclusion: The fixture documents the audit read-only brake. [file:doc.txt]\n"
    "Facts:\n- the audit read-only brake is documented [file:doc.txt]\n"
    "Sources:\n1. file:doc.txt - doc.txt\n"
    "Confidence: high\nUnverified: nothing\nSafety: nothing\n"
)


def _agent(root: Path, *, write_policy: MemoryWritePolicy | None = None) -> AgentLoop:
    root.mkdir(parents=True, exist_ok=True)
    (root / "doc.txt").write_text(
        "The audit read-only brake freezes durable memory writes.",
        encoding="utf-8",
    )
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    persistent_store = PersistentMemoryStore(data / "persistent_memory.jsonl")
    persistent_store.save(
        MemoryRecord(
            type="semantic",
            content="seed fact retained across the audit fixture run",
            tags=["seed"],
            owner="user",
        )
    )
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=root))
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_SYNTHESIS] * 4),
        logger=TraceLogger(new_trace_id(), root / "logs", verbose=False),
        planner=FakePlanner(
            sources=[
                {
                    "tool": "file_read",
                    "arguments": {"path": "doc.txt"},
                    "rationale": "read the fixture",
                    "label": "doc.txt",
                    "expected_outcome": "fixture text",
                }
            ]
        ),
        memory=WorkingMemory(),
        persistent_store=persistent_store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=write_policy or MemoryWritePolicy(),
        memory_write_registry=MemoryWriteRegistry(data / "memory_writes.jsonl"),
        source_registry_store=SourceRegistryStore(data / "source_registry.jsonl"),
        episodic_store=EpisodicMemoryStore(data / "episodic_memory.jsonl"),
        procedural_store=ProceduralMemoryStore(data / "procedural_memory.jsonl"),
        consolidation_store=MemoryConsolidationStore(
            data / "memory_consolidation.jsonl"
        ),
        knowledge_auto_write=True,
        user_profile_store=UserProfileStore(data / "user_profile.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        verifier_enabled=False,
        max_replan_attempts=1,
    )


def test_control_run_records_durable_learning(workspace: Path) -> None:
    """Baseline: without audit mode a normal cycle DOES grow durable memory.

    Guards the meaningfulness of the audit tests below — if the fixture never
    wrote anything, "audit suppresses writes" would be vacuously true.
    """
    agent = _agent(workspace / "control")
    agent.run(user_question=_GOAL)
    assert agent.episodic_store.load(), "fixture should write an episode without audit"


def test_audit_read_only_disables_all_durable_memory_writes(workspace: Path) -> None:
    agent = _agent(workspace / "audit")
    assert agent.set_audit_read_only(True) is True

    agent.run(user_question=_GOAL)

    assert agent.episodic_store.load() == []
    assert agent.procedural_store.load() == []
    assert agent.consolidation_store.count() == 0
    # Persistent access-stat bumps are a durable learning write too.
    assert agent.persistent_store.load()[0].access_count == 0
    # User profile must not learn from an audited interaction.
    assert agent.user_profile_store.load_or_default().interaction_count == 0


def test_audit_read_only_freezes_agent_auto_but_allows_user_explicit(
    workspace: Path,
) -> None:
    agent = _agent(workspace / "freeze")
    agent.set_audit_read_only(True)

    reject, rejected_record = agent.remember(
        content="agent-auto claim discovered while auditing memory",
        source="agent-auto",
    )
    assert reject.decision == "reject"
    assert rejected_record is None

    accept, saved_record = agent.remember(
        content="operator explicitly recorded a clear preference during the audit",
        source="user-explicit",
    )
    assert accept.decision == "save"
    assert saved_record is not None


def test_incomplete_audit_is_not_recorded_as_successful_procedure(
    workspace: Path,
) -> None:
    """An audited cycle banks NO procedure regardless of computed outcome.

    Outcome is evidence-quality based and does not know whether the planned
    investigation completed. Under audit read-only, durable learning is
    suppressed entirely, so an incomplete/uncertain audit can never graduate
    into a reusable "success" procedure.
    """
    agent = _agent(workspace / "incomplete")
    agent.set_audit_read_only(True)

    agent.run(user_question=_GOAL)

    assert agent.procedural_store.load() == []
    assert all(ep.outcome != "success" for ep in agent.episodic_store.load())


def test_audit_off_resumes_writes_and_preserves_env_freeze(workspace: Path) -> None:
    # An env-installed operator brake (AGENT_FREEZE_AUTO_MEMORY) pre-freezes
    # agent-auto. Toggling audit on then off must NOT lift that env freeze —
    # the audit only unfreezes what it itself installed.
    policy = MemoryWritePolicy(frozen_sources={"agent-auto"})
    agent = _agent(workspace / "envfreeze", write_policy=policy)

    assert agent.set_audit_read_only(True) is True
    assert agent._audit_froze_agent_auto is False  # env already had it frozen
    assert agent.set_audit_read_only(False) is False
    assert "agent-auto" in agent.write_policy.frozen_sources  # env freeze intact

    # After audit off (on a fresh policy) durable writes resume and the freeze
    # the audit installed is lifted.
    agent2 = _agent(workspace / "resume")
    agent2.set_audit_read_only(True)
    assert "agent-auto" in agent2.write_policy.frozen_sources
    agent2.set_audit_read_only(False)
    assert "agent-auto" not in agent2.write_policy.frozen_sources
    agent2.run(user_question=_GOAL)
    assert agent2.episodic_store.load(), "writes should resume once audit is off"


def test_set_audit_read_only_is_idempotent(workspace: Path) -> None:
    agent = _agent(workspace / "idem")
    assert agent.audit_read_only is False
    assert agent.set_audit_read_only(True) is True
    assert agent.set_audit_read_only(True) is True  # no double-freeze bookkeeping
    assert agent._audit_froze_agent_auto is True
    assert agent.set_audit_read_only(False) is False
    assert "agent-auto" not in agent.write_policy.frozen_sources


def test_audit_meta_command_toggles_state(workspace: Path) -> None:
    agent = _agent(workspace / "cmd")
    ws = workspace / "cmd"

    assert handle_meta_command(":audit on", agent, ws) is True
    assert agent.audit_read_only is True

    assert handle_meta_command(":audit status", agent, ws) is True
    assert agent.audit_read_only is True  # status must not change state

    assert handle_meta_command(":audit off", agent, ws) is True
    assert agent.audit_read_only is False

    assert handle_meta_command(":audit", agent, ws) is True  # bare == status
    assert agent.audit_read_only is False
