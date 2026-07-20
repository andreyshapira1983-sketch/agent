"""An episode may steer a later answer only if the task was actually done.

Until now every episodic reader keyed on `outcome`, which measures whether the
claims were supported. So the live store's one admitted episode was a blocked
non-answer: the evidence budget truncated the file, the agent said so honestly
with citations, and a well-supported non-answer read as `success` (MIR-057).

This commit gives the three episodic readers the second axis:

    decide_usage_eligibility   admission at banking
    the retrieval filter       what reaches the planner
    the fast path              what is replayed verbatim

All three consult the FROZEN `completion_state`, through one accessor, so
`None` cannot come to mean three different things. None of them looks at
`declared_completion` — what the model claimed is auditable history, not a
gate input — and none re-derives the verdict at read time, because feedback
for these episodes has already been spent under the rule in force when they
were banked.

Procedural credit and debit are deliberately untouched here; they are the next
commit, and one test pins that nothing moved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProcedureRecord,
    _compute_quality_score,
    decide_usage_eligibility,
    effective_completion,
    is_usage_eligible,
)

QUESTION = "how do I deploy the service"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _episode(
    eid: str = "ep-1",
    *,
    outcome: str = "success",
    completion: str | None = "achieved",
    tags: tuple[str, ...] = (),
    verified: int = 3,
    unverified: int = 0,
    eligible: bool | None = True,
    tools: tuple[str, ...] = (),
    answer: str = "The service deploys with `make deploy`.",
) -> EpisodeRecord:
    return EpisodeRecord(
        goal="deploy", question=QUESTION, outcome=outcome,  # type: ignore[arg-type]
        summary="deployed the service", tools_used=tools,
        verified_chunks=verified, unverified_chunks=unverified,
        # Computed the way the factory and `from_dict` compute it — the
        # dataclass default is None, and a None score is refused by the
        # quality gate (MIR-002), which would mask what these tests measure.
        answer_quality_score=_compute_quality_score(verified, unverified, 0),
        usage_eligible=eligible, completion_state=completion,  # type: ignore[arg-type]
        tags=tags, full_answer=answer, id=eid,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _seed(workspace: Path, episodes: list[EpisodeRecord]) -> None:
    store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
    for episode in episodes:
        store.save(episode)


def _retrieved(agent: AgentLoop) -> list[str]:
    agent._retrieve_experience_memory(QUESTION)
    return [ep.id for ep in agent._last_episode_records]


def _replayed(agent: AgentLoop, episode: EpisodeRecord) -> bool:
    """Does the fast path serve this episode instead of running a cycle?"""
    agent._last_best_similar_episode = episode
    agent._last_best_similar_score = 0.99
    return agent._fast_path_allows_replay(episode, 0.99)


# ==========================================================================
# 1. A well-supported non-answer is refused by all three readers.
# ==========================================================================
def test_a_blocked_success_fails_eligibility() -> None:
    """The live case: outcome says the claims held, completion says it did
    not answer."""
    assert decide_usage_eligibility(_episode(completion="blocked")) is False


def test_a_blocked_success_is_not_retrieved(tmp_path: Path) -> None:
    _seed(tmp_path, [_episode("blocked-ep", completion="blocked")])
    agent = _agent(tmp_path)

    assert _retrieved(agent) == []


def test_a_blocked_success_is_not_replayed(tmp_path: Path) -> None:
    agent = _agent(tmp_path)

    assert _replayed(agent, _episode(completion="blocked")) is False


# ==========================================================================
# 2 & 9. What worked before still works, and `achieved` buys nothing extra.
# ==========================================================================
def test_an_achieved_success_keeps_its_prior_behaviour(tmp_path: Path) -> None:
    _seed(tmp_path, [_episode("good")])
    agent = _agent(tmp_path)

    assert decide_usage_eligibility(_episode()) is True
    assert _retrieved(agent) == ["good"]
    assert _replayed(agent, _episode()) is True


@pytest.mark.parametrize(
    "kwargs,why",
    [
        ({"verified": 0, "unverified": 0}, "no measured evidence"),
        ({"answer": ""}, "nothing stored to replay"),
        ({"tools": ("shell_exec",)}, "the answer depends on world state"),
    ],
)
def test_achieved_does_not_bypass_the_existing_gates(kwargs: dict, why: str) -> None:
    """Monotonicity: the new axis only ever subtracts permission."""
    episode = _episode(**kwargs)
    assert effective_completion(episode) == "achieved"

    passes = decide_usage_eligibility(episode) and _quality_ok(episode)
    replayed = _fast_path_pure(episode)

    assert not (passes and replayed), f"achieved must not override: {why}"


def _quality_ok(episode: EpisodeRecord) -> bool:
    return AgentLoop._quality_allows_replay(episode)


def _fast_path_pure(episode: EpisodeRecord) -> bool:
    return bool(
        AgentLoop._quality_allows_replay(episode)
        and episode.full_answer
        and not episode.tools_used
        and effective_completion(episode) == "achieved"
    )


# ==========================================================================
# 3. The evidence axis still does its own job.
# ==========================================================================
@pytest.mark.parametrize("outcome", ["partial", "failed"])
def test_achieved_with_a_bad_outcome_is_still_refused(outcome: str) -> None:
    """Both axes must agree; neither is a substitute for the other."""
    assert decide_usage_eligibility(_episode(outcome=outcome, completion="achieved")) is False


# ==========================================================================
# 4. Every non-achieved state is refused for an ordinary episode.
# ==========================================================================
@pytest.mark.parametrize(
    "completion",
    ["partially_achieved", "blocked", "refused", "failed", "cancelled", "unknown", None],
)
def test_no_other_completion_state_admits_an_ordinary_episode(completion) -> None:
    episode = _episode(completion=completion)

    assert decide_usage_eligibility(episode) is False
    assert _fast_path_pure(episode) is False


# ==========================================================================
# 5. Readers key on the frozen state, never on the claim.
# ==========================================================================
@pytest.mark.parametrize("frozen", ["failed", "cancelled"])
def test_a_declared_achieved_cannot_override_the_frozen_state(frozen: str) -> None:
    """The model said it succeeded; the run says otherwise, and the run won at
    banking time. A reader must not re-open that."""
    episode = EpisodeRecord(
        goal="g", question=QUESTION, outcome="success", summary="s",
        verified_chunks=3, usage_eligible=True, full_answer="answer",
        declared_completion="achieved", completion_state=frozen,  # type: ignore[arg-type]
    )

    assert decide_usage_eligibility(episode) is False
    assert _fast_path_pure(episode) is False


def test_readers_never_consult_the_declaration() -> None:
    """A declaration with no frozen state is inert — that pairing only exists
    if something wrote the record by hand, and it must not be trusted."""
    episode = _episode(completion=None)
    object.__setattr__(episode, "declared_completion", "achieved")

    assert decide_usage_eligibility(episode) is False


# ==========================================================================
# 6. Legacy is withheld, not reconstructed.
# ==========================================================================
def test_a_legacy_row_is_withheld_without_reconstruction(tmp_path: Path) -> None:
    row = {
        "id": "ep-legacy", "goal": "g", "question": QUESTION, "outcome": "success",
        "summary": "s", "verified_chunks": 3, "unverified_chunks": 0,
        "usage_eligible": True, "full_answer": "answer",
    }
    episode = EpisodeRecord.from_dict(row)

    assert episode.completion_state is None
    assert effective_completion(episode) == "unknown", "one accessor, one answer"
    assert decide_usage_eligibility(episode) is False
    assert _fast_path_pure(episode) is False


def test_no_legacy_episode_in_the_live_store_is_ever_admitted(tmp_path: Path) -> None:
    """Legacy stays withheld — the invariant, not a snapshot.

    This first asserted that EVERY live episode read `unknown`, which was true
    the day it was written and false the moment the agent ran: new cycles bank
    real completion states, as they should. A test that pins a snapshot of
    mutable production data reports its own staleness as a regression.

    What must hold forever is narrower: an episode carrying no verdict — a row
    written before the axis existed — is never replayed, whatever else lands
    in the store around it.
    """
    store = EpisodicMemoryStore(Path("data/episodic_memory.jsonl"))
    episodes = store.load()
    if not episodes:
        pytest.skip("no live store in this environment")

    legacy = [ep for ep in episodes if ep.completion_state is None]
    assert all(effective_completion(ep) == "unknown" for ep in legacy)
    assert not [ep for ep in legacy if _fast_path_pure(ep)]
    # Modelled on what RETRIEVAL does, not on `decide_usage_eligibility`.
    # The banking-time policy admits a lesson whatever its stored bit says;
    # retrieval reads that bit, and a legacy row carries none. Asserting the
    # policy here would fail on the 108 legacy lessons while the live agent
    # admits none of them — the same conflation this suite exists to prevent.
    def _retrieval_admits(ep) -> bool:
        if "lesson" in ep.tags:
            return is_usage_eligible(ep)
        return effective_completion(ep) == "achieved" and is_usage_eligible(ep)

    assert not [ep for ep in legacy if _retrieval_admits(ep)], (
        "an unclassified episode must not reach a prompt as the store fills"
    )


# ==========================================================================
# 7. A lesson keeps its context arm and loses replay.
# ==========================================================================
def test_an_unknown_lesson_is_still_retrievable_as_context(tmp_path: Path) -> None:
    _seed(tmp_path, [
        _episode("a-lesson", outcome="failed", completion="unknown", tags=("lesson",))
    ])
    agent = _agent(tmp_path)

    assert decide_usage_eligibility(
        _episode(outcome="failed", completion="unknown", tags=("lesson",))
    ) is True, "learning from failure is what the tag is for"
    assert _retrieved(agent) == ["a-lesson"]


def test_an_unknown_lesson_is_not_replayable(tmp_path: Path) -> None:
    """Retrievable as a warning is not the same as reusable as an answer."""
    agent = _agent(tmp_path)
    lesson = _episode(outcome="failed", completion="unknown", tags=("lesson",))

    assert _replayed(agent, lesson) is False


# ==========================================================================
# 8. Nothing procedural moves in this commit.
# ==========================================================================
def test_no_procedural_counter_changes(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    seeded = ProcedureRecord(
        name="Workflow using file_read", workflow_key="tools:file_read",
        trigger_tags=("file_read",), steps=("Run tool: file_read",),
        source_episode_ids=(), success_count=2, failure_count=0,
        confidence=0.75, status="active",
    )
    agent.procedural_store.rewrite([seeded])
    _seed(tmp_path, [_episode("blocked-ep", completion="blocked")])

    agent._retrieve_experience_memory(QUESTION)

    after = agent.procedural_store.load()[0]
    assert (after.success_count, after.failure_count, after.confidence) == (2, 0, 0.75)


def test_the_rejection_reason_names_completion(tmp_path: Path) -> None:
    """The trace must say WHY, in the bounded vocabulary (MIR-055/056)."""
    import json

    _seed(tmp_path, [_episode("blocked-ep", completion="blocked")])
    agent = _agent(tmp_path)
    agent._retrieve_experience_memory(QUESTION)

    payload = [
        json.loads(line)["payload"]
        for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == "experience_memory_inject"
    ][-1]
    assert payload["rejected_by"].get("not_achieved") == 1
