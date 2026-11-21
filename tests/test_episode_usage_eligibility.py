"""Sub-step 2c — usage eligibility: stored is not the same as usable.

Episodes written before the quality defects (MIR-002/041/046) are fixed must
not steer answers. But "not usable" and "never classified" are different
states, and collapsing them would destroy information: a legacy row would
become indistinguishable from a deliberate quarantine decision.

Hence three states, not a boolean:

    None   legacy_unclassified — written before this field existed
    False  quarantined         — an explicit decision to withhold it
    True   eligible            — explicitly admitted to retrieval

Retrieval admits ONLY an explicit True. Both other states are fail-closed, so
until the quality defects are fixed the practical effect is that real stored
experience stops influencing answers: **wiring operational, usage
quarantined**. That is intended, not a regression.

The filter must cover all three paths that surface an episode: the ranked
`search`, the `lesson` top-up, and `find_most_similar` — the last of which
feeds both re-ask detection and the fast path.

Status when written: every test FAILS -- `EpisodeRecord` has no
`usage_eligible` field.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from tests.conftest import write_legacy_episode
from tests.test_memory_core_wiring import _drive_one_cycle


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


QUESTION = "how much is two plus two"


def _episode(*, usage_eligible: bool | None, eid: str = "ep-1") -> EpisodeRecord:
    return EpisodeRecord(
        goal="answer a general-knowledge question",
        question=QUESTION,
        outcome="success",  # type: ignore[arg-type]
        summary="Answered from general knowledge.",
        # This suite covers the eligibility BIT; completion (MIR-057) is the
        # second axis with its own suite, so seed past it.
        completion_state="achieved",
        verified_chunks=3,
        unverified_chunks=0,
        replan_exhausted=False,
        answer_quality_score=1.0,
        tools_used=[],
        full_answer="CACHED-ANSWER-FROM-EPISODIC-MEMORY",
        usage_eligible=usage_eligible,
        id=eid,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _interactive(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _seed(workspace: Path, episode: EpisodeRecord) -> EpisodicMemoryStore:
    """Seed one episode, preserving the eligibility state the test asked for.

    `usage_eligible=None` is the legacy state, and `save()` is the admission
    boundary — an episode that goes through it always comes out with an explicit
    verdict. A legacy row therefore has to be written the way the older schema
    wrote it: with the key absent. Everything else goes through `save()` as
    normal, where an explicit True/False is passed through untouched.
    """
    store = EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH)
    if episode.usage_eligible is None:
        write_legacy_episode(workspace / DEFAULT_EPISODIC_MEMORY_PATH, episode)
    else:
        store.save(episode)
    return store


def test_ineligible_lesson_is_not_topped_up(tmp_path: Path) -> None:
    """The `lesson` top-up path is a second door into retrieval; it must also filter."""
    lesson = EpisodeRecord(
        goal="repair core/foo.py", question="what broke in core/foo.py",
        outcome="failed",  # type: ignore[arg-type]
        summary="core/foo.py lost its guard clause",
        tags=("lesson",), usage_eligible=None, id="ep-lesson",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _seed(tmp_path, lesson)

    recalled = _interactive(tmp_path)._retrieve_experience_memory(
        "please repair core/foo.py"
    )
    assert "core/foo.py lost its guard" not in recalled


def test_fast_path_still_replays_an_eligible_episode(tmp_path: Path) -> None:
    """GUARD: the filter must not disable replay wholesale."""
    cached = _episode(usage_eligible=True)
    _seed(tmp_path, cached)

    answer = _drive_one_cycle(_interactive(tmp_path), QUESTION)
    assert answer.startswith(cached.full_answer) and "из памяти" in answer  # note line, 17.11
