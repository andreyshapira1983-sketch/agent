"""MIR-045 — hygiene must run without a human, and must be safe when it does.

Every hygiene operation already exists (`expire_persistent`, `dedupe_persistent`,
`prune_episodic`, `archive_low_value_memory`), but the only caller is the
`:hygiene` CLI command. On the unattended path nobody types it, so memory grows
until someone notices — which is how the earlier auto-memory pollution incident
happened.

The important part is not "call them on a timer". Hygiene DELETES, so the pass
is treated as what it is: a durable write. It gets its own sink in
`KNOWN_DURABLE_SINKS`, which means the existing policy governs it for free —
`:audit` and dry-run stop it absolutely, and a profile that has not been
granted the sink cannot run it. No separate permission branch to keep in sync.

Rollout is shadow-first: the default counts and logs what it would remove
without removing anything, because thresholds tuned against synthetic data are
exactly the thing you want to see reported before they touch a real store.

Status when written: every test FAILS — there is no automatic pass and no
`hygiene` sink.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.loop_methods2 import KNOWN_DURABLE_SINKS
from core.models import MemoryRecord
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path, durable_writes=frozenset({"hygiene"})) -> AgentLoop:
    return build_agent(
        workspace, with_memory=True, durable_writes=durable_writes,
        approval_provider=None,
    )


def _old_episode(eid: str, *, tags: tuple[str, ...] = (), quality: float | None = 0.0) -> EpisodeRecord:
    return EpisodeRecord(
        goal="g", question="q", outcome="failed",  # type: ignore[arg-type]
        summary="s", verified_chunks=0, unverified_chunks=5,
        answer_quality_score=quality, tags=tags, id=eid,
        created_at=(datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
    )


def _seed_prunable(workspace: Path) -> EpisodicMemoryStore:
    store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
    store.save(_old_episode("ordinary"))
    store.save(_old_episode("curated", tags=("lesson",)))
    return store


# ==========================================================================
# Hygiene is a durable write and is governed as one.
# ==========================================================================
def test_hygiene_is_a_known_durable_sink() -> None:
    assert "hygiene" in KNOWN_DURABLE_SINKS, (
        "hygiene deletes durable state, so it must be governed by the same "
        "policy as every other write rather than a parallel permission check"
    )


def test_audit_read_only_stops_the_pass(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    _seed_prunable(tmp_path)
    agent.set_audit_read_only(True)

    report = agent.run_maintenance_pass(dry_run=False)

    assert report["skipped"], "the audit brake must stop hygiene outright"
    assert len(EpisodicMemoryStore(tmp_path / DEFAULT_EPISODIC_MEMORY_PATH).load()) == 2


def test_dry_run_brake_stops_the_pass(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    _seed_prunable(tmp_path)
    agent.suppress_durable_learning_writes = True

    assert agent.run_maintenance_pass(dry_run=False)["skipped"]
    assert len(EpisodicMemoryStore(tmp_path / DEFAULT_EPISODIC_MEMORY_PATH).load()) == 2


def test_a_profile_without_the_sink_cannot_run_hygiene(tmp_path: Path) -> None:
    agent = _agent(tmp_path, durable_writes=frozenset({"episode"}))
    _seed_prunable(tmp_path)

    assert agent.run_maintenance_pass(dry_run=False)["skipped"]
    assert len(EpisodicMemoryStore(tmp_path / DEFAULT_EPISODIC_MEMORY_PATH).load()) == 2


# ==========================================================================
# Shadow first.
# ==========================================================================
def test_shadow_pass_reports_without_removing(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    store = _seed_prunable(tmp_path)

    report = agent.run_maintenance_pass(dry_run=True)

    assert not report.get("skipped")
    assert report["dry_run"] is True
    assert report["episodes_pruned"] >= 1, "shadow must still report what it would remove"
    assert len(store.load()) == 2, "shadow must not remove anything"


def test_enforcing_pass_actually_removes(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    store = _seed_prunable(tmp_path)

    report = agent.run_maintenance_pass(dry_run=False)

    assert report["dry_run"] is False
    assert len(store.load()) < 2, "the enforcing pass must remove the stale episode"


# ==========================================================================
# Safety: what must survive a pass.
# ==========================================================================
def test_curated_lesson_survives_the_pass(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    store = _seed_prunable(tmp_path)

    agent.run_maintenance_pass(dry_run=False)

    assert "curated" in {e.id for e in store.load()}, (
        "a lesson episode is learn-from-failure by design and must never be "
        "pruned, however stale it looks"
    )


def test_user_explicit_records_are_not_archived(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.persistent_store.save(
        MemoryRecord(
            content="Operator: always deploy on Fridays at noon.",
            tags=["preference"], owner="user", source="user-explicit",
            created_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
    )

    agent.run_maintenance_pass(dry_run=False)

    remaining = [r.content for r in agent.persistent_store.load()]
    assert any("Fridays" in c for c in remaining), (
        "an operator-written record must not be archived by an unattended pass"
    )


def test_pass_reports_every_stage(tmp_path: Path) -> None:
    """The delta report is the only way an operator sees what ran unattended."""
    agent = _agent(tmp_path)
    _seed_prunable(tmp_path)

    report = agent.run_maintenance_pass(dry_run=True)

    for stage in ("expired", "deduped", "episodes_pruned", "archived"):
        assert stage in report, f"stage {stage!r} missing from the delta report"
