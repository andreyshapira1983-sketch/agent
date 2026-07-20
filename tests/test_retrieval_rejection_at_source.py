"""A rejection reason must come from the component that rejected, not from
arithmetic performed by the observer.

MIR-055 gave retrieval a `rejected_by` trace, but the persistent path built it
by subtracting list lengths:

    ("role_scope",      len(records) - len(use_report.allowed))
    ("below_threshold", len(use_report.allowed) - len(selected))

Subtraction cannot separate causes it never saw. Measured on the live store
(103 records), 4 of 6 realistic questions were reported with the wrong reason —
worst case `procedural confidence beta smoothing`, logged as
`below_threshold: 100` when the truth was `below_threshold: 28, over_limit: 72`.
"almost nothing here is relevant" and "72 relevant records were thrown away by
a cap of 3" call for opposite responses, which is the very sentence MIR-055
was written to make sayable.

The same shape on the experience path: the loop counts only what
`EpisodicMemoryStore.search` handed back, so everything the search itself
dropped is invisible. On the live store (200 episodes) the question
"quarterly revenue in singapore" logs `episodes_selected=0, rejected_by={}` —
the unreadable line MIR-055 set out to eliminate, still reachable.

So the fix is not more subtraction. Each decider reports its own reasons, and
the observer merges what it was told.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.memory_policy import MemoryRetrievalPolicy
from core.models import MemoryRecord
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore

QUESTION = "how do I deploy the service"
STOPWORDS_ONLY = "и в"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _record(content: str, *, tags: tuple[str, ...] = ("fact",)) -> MemoryRecord:
    return MemoryRecord(
        type="semantic", content=content, tags=list(tags),
        owner="self", source="agent-auto",
    )


def _episode(eid: str, *, goal: str = "deploy", question: str = QUESTION,
             summary: str = "deployed the service", outcome: str = "success",
             eligible: bool | None = True) -> EpisodeRecord:
    return EpisodeRecord(
        goal=goal, question=question, outcome=outcome,  # type: ignore[arg-type]
        summary=summary, tools_used=("shell_exec",),
        verified_chunks=2, unverified_chunks=0, usage_eligible=eligible,
        id=eid, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _unrelated(eid: str) -> EpisodeRecord:
    """No token in common with QUESTION — goal included, which is in the haystack."""
    return _episode(eid, goal="бюджет", question="бюджет на квартал",
                    summary="подготовил смету")


def _seed_episodes(workspace: Path, episodes: list[EpisodeRecord]) -> None:
    store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
    for episode in episodes:
        store.save(episode)


def _event(agent: AgentLoop, name: str) -> dict:
    import json
    for line in reversed(Path(agent.log.path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == name:
            return event.get("payload", {})
    raise AssertionError(f"event {name!r} was never logged")


# ==========================================================================
# The root: a policy that decides must be able to say why.
# ==========================================================================
def test_the_retrieval_policy_reports_why_it_dropped_records() -> None:
    policy = MemoryRetrievalPolicy(max_records=2)
    records = [_record(f"deploy the service step {i}") for i in range(5)]

    report = policy.select_with_report(records, QUESTION)

    assert len(report.selected) == 2
    assert report.rejected_by == {"over_limit": 3}, (
        "three records matched and were cut by max_records — that is a cap, "
        "not a relevance floor, and the caller cannot tell them apart"
    )


def test_the_policy_separates_a_relevance_floor_from_a_cap() -> None:
    policy = MemoryRetrievalPolicy(max_records=2)
    records = [_record(f"deploy the service step {i}") for i in range(4)]
    records += [_record("подготовка бюджета на квартал", tags=("fact",))]

    report = policy.select_with_report(records, QUESTION)

    assert report.rejected_by == {"over_limit": 2, "below_threshold": 1}


def test_a_question_with_no_tokens_is_not_the_records_fault() -> None:
    policy = MemoryRetrievalPolicy()
    records = [_record(f"deploy the service step {i}") for i in range(3)]

    report = policy.select_with_report(records, STOPWORDS_ONLY)

    assert report.selected == []
    assert report.rejected_by == {"no_query_tokens": 3}, (
        "the question produced no searchable tokens; blaming the records "
        "sends the reader to the wrong store"
    )


def test_select_returns_exactly_what_the_report_selected() -> None:
    """One decision implementation, not two that can drift apart."""
    policy = MemoryRetrievalPolicy(max_records=2)
    records = [_record(f"deploy the service step {i}") for i in range(5)]

    assert policy.select(records, QUESTION) == policy.select_with_report(records, QUESTION).selected


def test_every_candidate_is_accounted_for_exactly_once() -> None:
    """The invariant that makes subtraction unnecessary forever."""
    policy = MemoryRetrievalPolicy(max_records=2)
    records = [_record(f"deploy the service step {i}") for i in range(4)]
    records += [_record(f"unrelated budget note {i}") for i in range(3)]

    report = policy.select_with_report(records, QUESTION)

    assert len(report.selected) + sum(report.rejected_by.values()) == len(records)


def test_episodic_search_reports_its_own_drops(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "episodes.jsonl")
    for i in range(5):
        store.save(_episode(f"match-{i}"))
    for i in range(2):
        store.save(_unrelated(f"other-{i}"))

    result = store.search_with_report(QUESTION, limit=3)

    assert len(result.episodes) == 3
    assert result.rejected_by == {"over_limit": 2, "no_overlap": 2}
    assert len(result.episodes) + sum(result.rejected_by.values()) == 7


# ==========================================================================
# The observer stops inferring.
# ==========================================================================
def test_capped_records_are_not_logged_as_below_threshold(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    for i in range(6):
        agent.persistent_store.save(_record(f"deploy the service note {i}"))

    agent._retrieve_persistent(QUESTION)

    data = _event(agent, "persistent_memory_inject")
    assert data["records_selected"] == 3
    assert data["rejected_by"] == {"over_limit": 3}, (
        "six relevant records, three shown — the three unshown were capped, "
        "not judged irrelevant"
    )


def test_a_quarantined_record_is_not_logged_as_role_scope(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.persistent_store.save(
        _record("deploy the service via the old runbook", tags=("fact", "obsolete"))
    )

    agent._retrieve_persistent(QUESTION)

    data = _event(agent, "persistent_memory_inject")
    assert data["rejected_by"] == {"quarantined": 1}, (
        "a record withheld because it is marked obsolete is not a record "
        "outside the role's scope — the responses differ"
    )


def test_episodes_dropped_inside_search_are_still_reported(tmp_path: Path) -> None:
    _seed_episodes(tmp_path, [_unrelated(f"other-{i}") for i in range(4)])
    agent = _agent(tmp_path)

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    assert data["episodes_selected"] == 0
    assert data["rejected_by"] == {"no_overlap": 4}, (
        "`selected=0, rejected_by={}` is the unreadable line MIR-055 exists "
        "to prevent; drops inside search() are still drops"
    )


def test_experience_retrieval_accounts_for_every_episode(tmp_path: Path) -> None:
    _seed_episodes(tmp_path, [
        *[_episode(f"good-{i}") for i in range(5)],
        _episode("bad", outcome="failed"),
        _episode("held", eligible=None),
        _unrelated("far"),
    ])
    agent = _agent(tmp_path)

    agent._retrieve_experience_memory(QUESTION)

    data = _event(agent, "experience_memory_inject")
    total = data["episodes_selected"] + sum(data["rejected_by"].values())
    assert total == 8, (
        f"8 episodes in the store, {total} accounted for — an unaccounted "
        "episode is exactly the blind spot this trace is for"
    )
