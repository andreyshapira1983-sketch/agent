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

import json
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
)

QUESTION = "how do I deploy the service"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _retrieved(agent: AgentLoop) -> list[str]:
    agent._retrieve_experience_memory(QUESTION)
    return [ep.id for ep in agent._last_episode_records]


def _replayed(agent: AgentLoop, episode: EpisodeRecord) -> bool:
    """Does the fast path serve this episode instead of running a cycle?"""
    agent._last_best_similar_episode = episode
    agent._last_best_similar_score = 0.99
    return agent._fast_path_allows_replay(episode, 0.99)


def test_no_legacy_episode_in_the_live_store_is_ever_admitted(tmp_path: Path) -> None:
    """The live store's legacy rows, seeded: none reaches a prompt or is replayed.

    Written as raw JSON because `store.save` stamps the axis and the bit on the way in.
    """
    common = {
        "goal": "deploy", "question": QUESTION, "summary": "deployed the service",
        "verified_chunks": 3, "unverified_chunks": 0,
        "full_answer": "The service deploys with `make deploy`.",
    }
    rows = [
        # Legacy lessons carry no eligibility bit.
        {**common, "id": "legacy-lesson", "outcome": "failed", "tags": ["lesson"]},
        # A legacy success kept the bit it was banked with, but no verdict.
        {**common, "id": "legacy-success", "outcome": "success", "usage_eligible": True},
    ]
    path = tmp_path / DEFAULT_EPISODIC_MEMORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    agent = _agent(tmp_path)
    legacy = EpisodicMemoryStore(path).load()
    assert [ep.completion_state for ep in legacy] == [None, None]

    assert _retrieved(agent) == [], (
        "an unclassified episode must not reach a prompt as the store fills"
    )
    assert not [ep.id for ep in legacy if _replayed(agent, ep)]
