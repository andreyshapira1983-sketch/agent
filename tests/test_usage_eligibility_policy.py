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

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.model_router import ModelRouter
from tests.conftest import FakeLLM, FakePlanner
from tests.test_smart_memory import _declare_completion


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


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
