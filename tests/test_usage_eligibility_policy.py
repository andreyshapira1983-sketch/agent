"""Lifting the quarantine — through an explicit admission policy, not a constant.

Episodes have been written `usage_eligible=False` since 2d, because the
semantics that scored them were the ones MIR-002/041/046 describe as broken.
Those are now fixed, so new episodes may be admitted. Flipping the literal to
`True` would admit *everything* — including runs that failed, replays, and
answers that carried no evidence at all. Admission is a decision, so it gets a
function.

`decide_usage_eligibility` admits an episode only when all of these hold:

    outcome == "success"        the cycle actually completed and was scored
    verified_chunks > 0         something was independently confirmed
    not a replay                a copy of an earlier answer is not experience

with one deliberate exception: a curated `lesson` is admitted regardless of
outcome — learning from a failure is the entire point of the tag.

Fail-closed everywhere else. Note what this excludes and why:

  - a pure general-knowledge answer (no chunks at all) is NOT admitted: there
    is nothing in it to reuse — no sources, no verified findings. It is a fine
    answer and a poor memory.
  - a `partial` outcome is not admitted: unverified support outnumbered
    verified, which is precisely the self-reinforcement MIR-001 closed.
  - a replay banks verified_chunks=0 after MIR-041, so it is refused by the
    evidence rule without needing a special case — the source-label check is
    defence in depth.

No threshold constant appears anywhere: every rule reads a measured fact.

Status when written: every test FAILS — `decide_usage_eligibility` does not exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.model_router import ModelRouter
from core.smart_memory import EpisodeRecord, decide_usage_eligibility
from tests.conftest import FakeLLM, FakePlanner, write_legacy_episode
from tests.test_memory_core_wiring import _drive_one_cycle
from tests.test_smart_memory import _declare_completion

QUESTION = "how much is two plus two"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _ep(
    *,
    outcome: str = "success",
    verified: int = 3,
    unverified: int = 0,
    tags: tuple[str, ...] = (),
    labels: tuple[str, ...] = ("file:atlas.txt",),
    completion: str | None = "achieved",
) -> EpisodeRecord:
    # Admission now needs BOTH axes (MIR-057): these cases vary the evidence
    # axis, so completion is held at `achieved` unless a case says otherwise.
    return EpisodeRecord(
        goal="g", question=QUESTION, outcome=outcome,  # type: ignore[arg-type]
        summary="s", completion_state=completion,  # type: ignore[arg-type]
        verified_chunks=verified, unverified_chunks=unverified,
        answer_quality_score=(
            None if (verified + unverified) == 0 else verified / (verified + unverified)
        ),
        tags=tags, source_labels=labels,
        id="ep-policy", created_at=datetime.now(timezone.utc).isoformat(),
    )


# ==========================================================================
# The admission matrix.
# ==========================================================================
@pytest.mark.parametrize(
    "case,episode_kwargs,expected",
    [
        ("verified success",        {"outcome": "success", "verified": 3, "unverified": 0}, True),
        ("mostly verified success", {"outcome": "success", "verified": 8, "unverified": 2}, True),
        ("success, no evidence",    {"outcome": "success", "verified": 0, "unverified": 0}, False),
        ("success, only unverified", {"outcome": "success", "verified": 0, "unverified": 5}, False),
        ("partial outcome",         {"outcome": "partial", "verified": 3, "unverified": 9}, False),
        ("failed outcome",          {"outcome": "failed",  "verified": 3, "unverified": 0}, False),
        ("replay",                  {"outcome": "success", "verified": 0, "unverified": 1,
                                         "labels": ("memory:ep_prev",)},                  False),
        ("lesson, failed",          {"outcome": "failed",  "verified": 0, "unverified": 1,
                                         "tags": ("lesson",)},                            True),
    ],
)
def test_admission_matrix(case: str, episode_kwargs: dict, expected: bool) -> None:
    assert decide_usage_eligibility(_ep(**episode_kwargs)) is expected, case


# ==========================================================================
# Integration — the loop must actually consult the policy.
# ==========================================================================
_ATLAS_ANSWER = (
    "Conclusion: The atlas has twelve maps [file:atlas.txt].\n"
    "Facts:\n- The atlas has twelve maps [file:atlas.txt]\n"
    "- The first map shows the northern coast [file:atlas.txt]\n"
    "Sources:\n1. file:atlas.txt - atlas.txt\n"
    "Confidence: high\n"
)


def test_verified_cycle_banks_an_admitted_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An evidenced run becomes reusable experience end to end.

    Only `True` proves the loop asked the policy: a hard-coded quarantine
    `False` is also "not None".
    """
    (tmp_path / "atlas.txt").write_text(
        "The atlas has twelve maps. The first map shows the northern coast.\n",
        encoding="utf-8",
    )
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    agent.planner = FakePlanner(sources=[{
        "tool": "file_read", "arguments": {"path": "atlas.txt"},
        "label": "file:atlas.txt", "expected_outcome": "the atlas text",
    }])
    agent.llm = FakeLLM(responses=[_ATLAS_ANSWER] * 4)
    agent.model_router = ModelRouter.single(agent.llm)
    _declare_completion(monkeypatch)

    agent.run(user_question="how many maps does the atlas have", file_hint="atlas.txt")

    banked = agent.episodic_store.load()
    assert len(banked) == 1, "the cycle must bank one episode"
    episode = banked[0]
    assert (episode.outcome, episode.completion_state) == ("success", "achieved"), episode
    assert episode.verified_chunks > 0, "fixture drifted: the citation no longer verifies"
    assert episode.usage_eligible is True, (
        "the loop still defaults instead of consulting the admission policy"
    )


def test_ungrounded_cycle_is_not_admitted(tmp_path: Path) -> None:
    """The fake cycle cites unresolvable sources, so nothing is confirmed."""
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    _drive_one_cycle(agent, QUESTION)

    banked = agent.episodic_store.load()[0]
    assert banked.usage_eligible is False, (
        f"an unverified cycle ({banked.verified_chunks} verified / "
        f"{banked.unverified_chunks} unverified) must not become reusable experience"
    )


def test_legacy_episodes_stay_excluded_after_the_lift(tmp_path: Path) -> None:
    """Lifting the quarantine must not retroactively admit unclassified rows."""
    # A row from before the field existed — written the way that schema wrote
    # it, since `save()` now always attaches a verdict.
    write_legacy_episode(
        tmp_path / DEFAULT_EPISODIC_MEMORY_PATH, _ep(verified=9, unverified=0)
    )
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)

    assert not agent._retrieve_experience_memory(QUESTION).strip(), (
        "a legacy row was admitted by the lift — None must stay fail-closed"
    )
