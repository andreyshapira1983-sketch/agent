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

QUESTION = "how do I deploy the service"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _episode(eid: str, *, outcome: str = "success", eligible: bool | None = True,
             tags: tuple[str, ...] = ()) -> EpisodeRecord:
    return EpisodeRecord(
        goal="deploy", question=QUESTION, outcome=outcome,  # type: ignore[arg-type]
        summary="deployed the service", tools_used=("shell_exec",),
        verified_chunks=2, unverified_chunks=0, usage_eligible=eligible,
        tags=tags, id=eid, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _seed(workspace: Path, episodes: list[EpisodeRecord]) -> None:
    store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
    for episode in episodes:
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


# ==========================================================================
# Experience retrieval — the case that prompted this.
# ==========================================================================
def test_quarantined_episodes_are_reported_as_the_reason(tmp_path: Path) -> None:
    """`selected=0` must be distinguishable from `nothing matched`."""
    _seed(tmp_path, [_episode(f"ep-{i}", eligible=None) for i in range(4)])
    agent = _agent(tmp_path)

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert data["episodes_selected"] == 0
    assert data["rejected_by"]["not_eligible"] == 4, (
        "a cycle that withheld everything must say so, not just report zero"
    )


def test_rejection_reasons_are_separated(tmp_path: Path) -> None:
    _seed(tmp_path, [
        _episode("good"),                                  # selected
        _episode("failed-run", outcome="failed"),          # wrong outcome
        _episode("quarantined", eligible=False),           # withheld
        _episode("legacy", eligible=None),                 # unclassified
    ])
    agent = _agent(tmp_path)

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert data["episodes_selected"] == 1
    assert data["rejected_by"]["outcome"] == 1
    assert data["rejected_by"]["not_eligible"] == 2, "False and None are both withheld"


def test_no_rejections_reports_an_empty_mapping(tmp_path: Path) -> None:
    _seed(tmp_path, [_episode("good")])
    agent = _agent(tmp_path)

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert data["episodes_selected"] == 1
    assert data["rejected_by"] == {}, "absent reasons must not appear as zeros"


def test_retrieval_disabled_is_itself_a_reason(tmp_path: Path) -> None:
    """The unattended profile can hold stores while being denied reads."""
    _seed(tmp_path, [_episode("good")])
    agent = build_agent(
        tmp_path, with_memory=True, experience_retrieval=False, approval_provider=None
    )

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert data["rejected_by"] == {"retrieval_disabled": 1}


def test_counts_are_aggregated_not_per_record(tmp_path: Path) -> None:
    """Volume guard: the trace must not grow with the store."""
    _seed(tmp_path, [_episode(f"ep-{i}", eligible=None) for i in range(30)])
    agent = _agent(tmp_path)

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert isinstance(data["rejected_by"], dict)
    assert len(data["rejected_by"]) <= 4, (
        "reasons are a fixed vocabulary; one entry per rejected record would "
        "make the trace cost more than the retrieval it observes"
    )


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
