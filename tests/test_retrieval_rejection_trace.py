"""Retrieval must say what it rejected and why, not only what it chose.

Today a cycle logs `episodes_selected=0` with two hundred episodes sitting in
the store. That is a true statement and a useless one: it cannot distinguish
"nothing matched the question" from "everything matched but was quarantined",
and those call for opposite responses.

The counterfactual side is where the information is. Three defects this week
were found by reading a live log; each time the missing piece was *why a
record did not come back*.

Counts are aggregated **by reason**, never per record. A trace that emitted one
line per rejected episode would cost more than the retrieval it observes —
exactly the failure mode of switching a mechanism on without checking the
volume of its input.

`rejected_by` is therefore a small dict, absent keys meaning zero:

    {"not_eligible": 200}          everything was withheld
    {"outcome": 3, "not_eligible": 1}
    {}                             nothing was rejected
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from tests.conftest import write_legacy_episode

QUESTION = "how do I deploy the service"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _episode(eid: str, *, outcome: str = "success", eligible: bool | None = True,
             tags: tuple[str, ...] = (),
             completion: str | None = "achieved") -> EpisodeRecord:
    # This suite is about the REASONS retrieval reports, so every episode
    # clears the completion gate (MIR-057) unless a case is about that gate.
    return EpisodeRecord(
        goal="deploy", question=QUESTION, outcome=outcome,  # type: ignore[arg-type]
        summary="deployed the service", tools_used=("shell_exec",),
        verified_chunks=2, unverified_chunks=0, usage_eligible=eligible,
        completion_state=completion,  # type: ignore[arg-type]
        tags=tags, id=eid, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _seed(workspace: Path, episodes: list[EpisodeRecord]) -> None:
    """Seed episodes, keeping `eligible=None` genuinely unclassified.

    `save()` is the admission boundary, so it cannot produce the legacy state;
    a row that predates the field is written the way that schema wrote it.
    """
    path = workspace / DEFAULT_EPISODIC_MEMORY_PATH
    store = EpisodicMemoryStore(path)
    for episode in episodes:
        if episode.usage_eligible is None:
            write_legacy_episode(path, episode)
        else:
            store.save(episode)


def _event(agent: AgentLoop, name: str) -> dict:
    import json
    lines = Path(agent.log.path).read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == name:
            return event.get("payload", {})
    raise AssertionError(f"event {name!r} was never logged")


def test_retrieval_disabled_is_itself_a_reason(tmp_path: Path) -> None:
    """The unattended profile can hold stores while being denied reads."""
    _seed(tmp_path, [_episode("good")])
    agent = build_agent(
        tmp_path, with_memory=True, experience_retrieval=False, approval_provider=None
    )

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert data["rejected_by"] == {"retrieval_disabled": 1}


# ==========================================================================
# Persistent retrieval.
# ==========================================================================
def test_persistent_retrieval_reports_why_records_did_not_surface(tmp_path: Path) -> None:
    from core.models import MemoryRecord

    agent = _agent(tmp_path)
    for i in range(3):
        agent.persistent_store.save(MemoryRecord(
            type="semantic", content=f"unrelated fact number {i}",
            tags=["fact"], owner="self", source="agent-auto",
        ))

    agent._retrieve_persistent(QUESTION)

    data = _event(agent, "persistent_memory_inject")
    assert "rejected_by" in data, "persistent retrieval must explain its zero too"
    assert data["rejected_by"].get("below_threshold", 0) >= 1
