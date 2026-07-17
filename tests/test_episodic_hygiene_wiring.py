"""Wiring test: episodic pruning is reachable from the agent + :hygiene CLI.

The staleness scoring/selection itself is covered by test_episodic_hygiene.py.
This file pins the INTEGRATION that was previously missing: the agent exposes
``prune_episodic`` and the ``:hygiene`` REPL chain actually invokes it, so the
FIFO-distractor failure mode episodic_hygiene was written to fix is really
closed in production.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from cli.commands_memory import _handle_hygiene
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _ep(*, eid: str, age_days: float, quality: float, outcome: str = "success",
        tags: tuple[str, ...] = (), replan_exhausted: bool = False) -> EpisodeRecord:
    created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    return EpisodeRecord(
        goal="g", question="q", outcome=outcome, summary="s",  # type: ignore[arg-type]
        verified_chunks=1, unverified_chunks=0,
        replan_exhausted=replan_exhausted,
        answer_quality_score=quality, tags=tags, id=eid, created_at=created,
    )


def _agent(workspace: Path, *, episodic_store: EpisodicMemoryStore | None) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    llm = FakeLLM(responses=[])
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry),
        approval_provider=AutoApprover(default="approve"),
        episodic_store=episodic_store,
    )


def _seed(store: EpisodicMemoryStore) -> None:
    # old + low-quality + failed → prunable distractor
    store.save(_ep(eid="junk", age_days=90, quality=0.0, outcome="failed",
                   replan_exhausted=True))
    # old but high-quality success → kept
    store.save(_ep(eid="good", age_days=90, quality=1.0, outcome="success"))
    # old + low-quality but PROTECTED → kept
    store.save(_ep(eid="lesson", age_days=90, quality=0.0, outcome="failed",
                   tags=("lesson",)))
    # recent junk → kept (loop can still learn from recent failures)
    store.save(_ep(eid="recent", age_days=1, quality=0.0, outcome="failed"))


def test_prune_episodic_removes_only_stale_distractors(workspace: Path):
    store = EpisodicMemoryStore(workspace / "data" / "episodic.jsonl")
    _seed(store)
    agent = _agent(workspace, episodic_store=store)

    # Dry-run reports the victim but does not delete it.
    dry = agent.prune_episodic(dry_run=True)
    assert dry == ["junk"]
    assert {e.id for e in store.load()} == {"junk", "good", "lesson", "recent"}

    # Real run removes only the stale distractor.
    pruned = agent.prune_episodic()
    assert pruned == ["junk"]
    assert {e.id for e in store.load()} == {"good", "lesson", "recent"}


def test_prune_episodic_noop_without_store(workspace: Path):
    agent = _agent(workspace, episodic_store=None)
    assert agent.prune_episodic() == []


def test_hygiene_episodic_subcommand_prunes(workspace: Path, capsys):
    store = EpisodicMemoryStore(workspace / "data" / "episodic.jsonl")
    _seed(store)
    agent = _agent(workspace, episodic_store=store)

    assert _handle_hygiene("episodic", agent, workspace) is True
    out = capsys.readouterr().err
    assert "1 stale episode(s) pruned" in out
    assert "junk" in out
    assert {e.id for e in store.load()} == {"good", "lesson", "recent"}


def test_hygiene_all_chain_includes_episodic(workspace: Path, capsys):
    store = EpisodicMemoryStore(workspace / "data" / "episodic.jsonl")
    _seed(store)
    agent = _agent(workspace, episodic_store=store)

    assert _handle_hygiene("all", agent, workspace) is True
    out = capsys.readouterr().err
    assert "episodic : 1 stale episode(s) pruned" in out
    assert {e.id for e in store.load()} == {"good", "lesson", "recent"}
