"""Sub-step 2d — autonomous episode write-back, and only that.

The unattended agent starts banking episodes. Everything else it could write
stays denied: no procedural promotion, no consolidation, no knowledge, no
source registry, no profile, no assumptions.

Two safety properties matter more than the feature:

1. **A run that did not finish is never banked as success.** `outcome` is
   otherwise computed from chunk counts alone, which know nothing about an
   exception, a cancellation or a timeout. Cancellation is handled explicitly
   and RE-RAISED -- it is not an error to be swallowed.

2. **Autonomous episodes are quarantined, not unclassified.** They are written
   `usage_eligible=False`: an explicit decision to withhold, distinct from
   legacy rows that were never classified. So write-back closes the loop
   mechanically while the quality defects (MIR-002/041/046) keep it inert --
   wiring operational, usage quarantined.

Status when written: the write-back tests FAIL (the unattended profile has an
empty allowlist) and the safety tests FAIL (nothing records an aborted run).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_tick import UNATTENDED_MEMORY_PROFILE
from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.smart_memory import EpisodicMemoryStore
from tests.test_memory_core_wiring import (
    SYNTH_GENERAL,
    _durable_snapshot,
    _drive_one_cycle,
)
from tests.conftest import FakeLLM, FakePlanner

QUESTION = "how much is two plus two"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _autonomous(workspace: Path) -> AgentLoop:
    return build_agent(workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE)


def _episodes(workspace: Path):
    return EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH).load()


class _ExplodingPlanner:
    """A planner that fails the way a real one might mid-cycle."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def plan(self, **kwargs):
        raise self.exc


def _drive_until_it_raises(agent: AgentLoop, exc: BaseException) -> None:
    agent.planner = _ExplodingPlanner(exc)
    agent.llm = FakeLLM(responses=[SYNTH_GENERAL] * 4)
    agent.run(user_question=QUESTION, file_hint=None, task_id="T-abort")


# ==========================================================================
# Write-back: the episode sink, and nothing else.
# ==========================================================================
def test_autonomous_cycle_banks_exactly_one_episode(tmp_path: Path) -> None:
    agent = _autonomous(tmp_path)
    before = _durable_snapshot(tmp_path)

    assert _drive_one_cycle(agent, QUESTION), "the cycle must produce an answer"

    after = _durable_snapshot(tmp_path)
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {str(Path("data") / "episodic_memory.jsonl")}, (
        f"only the episode sink may change; got: {sorted(changed)}"
    )
    assert len(_episodes(tmp_path)) == 1


def test_autonomous_episode_is_quarantined_not_unclassified(tmp_path: Path) -> None:
    """False (withheld on purpose) must be distinguishable from None (legacy)."""
    _drive_one_cycle(_autonomous(tmp_path), QUESTION)

    banked = _episodes(tmp_path)[0]
    assert banked.usage_eligible is False, (
        "an episode written under known-broken quality semantics is an explicit "
        "quarantine decision, not an unclassified legacy row"
    )


def test_autonomous_episode_carries_run_id(tmp_path: Path) -> None:
    _drive_one_cycle(_autonomous(tmp_path), QUESTION, task_id="T-9")

    banked = _episodes(tmp_path)[0]
    assert banked.run_id, "the attempt that produced it must be identifiable"
    assert banked.task_id == "T-9"


def test_banked_autonomous_episode_is_not_fed_back(tmp_path: Path) -> None:
    """The whole point of quarantining: the loop closes but stays inert."""
    agent = _autonomous(tmp_path)
    _drive_one_cycle(agent, QUESTION)
    assert len(_episodes(tmp_path)) == 1

    recalled = _autonomous(tmp_path)._retrieve_experience_memory(QUESTION)
    assert not recalled.strip(), (
        "a quarantined episode must not come back as experience"
    )


# ==========================================================================
# Safety: an unfinished run is never a success.
# ==========================================================================
def test_exception_is_not_banked_as_success(tmp_path: Path) -> None:
    agent = _autonomous(tmp_path)

    with pytest.raises(RuntimeError):
        _drive_until_it_raises(agent, RuntimeError("planner exploded"))

    banked = _episodes(tmp_path)
    assert banked, "a failed run must leave a trace, not vanish"
    assert banked[0].outcome != "success", (
        f"a run that raised was banked as {banked[0].outcome!r}"
    )


def test_cancellation_is_reraised_and_not_banked_as_success(tmp_path: Path) -> None:
    """Cancellation is a control signal, not an error: it must propagate."""
    agent = _autonomous(tmp_path)

    with pytest.raises(asyncio.CancelledError):
        _drive_until_it_raises(agent, asyncio.CancelledError())

    banked = _episodes(tmp_path)
    assert banked and banked[0].outcome != "success"


def test_keyboard_interrupt_is_reraised_and_not_banked_as_success(tmp_path: Path) -> None:
    agent = _autonomous(tmp_path)

    with pytest.raises(KeyboardInterrupt):
        _drive_until_it_raises(agent, KeyboardInterrupt())

    banked = _episodes(tmp_path)
    assert banked and banked[0].outcome != "success"


def test_aborted_episode_is_also_quarantined(tmp_path: Path) -> None:
    agent = _autonomous(tmp_path)
    with pytest.raises(RuntimeError):
        _drive_until_it_raises(agent, RuntimeError("boom"))

    assert _episodes(tmp_path)[0].usage_eligible is False


def test_one_abort_banks_one_episode(tmp_path: Path) -> None:
    """Idempotency keys on run_id, so the abort path cannot double-write."""
    agent = _autonomous(tmp_path)
    with pytest.raises(RuntimeError):
        _drive_until_it_raises(agent, RuntimeError("boom"))

    assert len(_episodes(tmp_path)) == 1


# ==========================================================================
# GUARD: the happy path is unchanged.
# ==========================================================================
def test_completed_cycle_is_not_treated_as_aborted(tmp_path: Path) -> None:
    """A cycle that finished must be scored by its evidence, not by the abort path.

    The concrete outcome here is `partial` rather than `success` — this fake
    cycle cites sources the verifier cannot resolve, so unverified chunks
    outnumber verified ones (the MIR-001 rule). That is correct scoring. What
    this guards is that a COMPLETED run never lands on the aborted branch,
    which would force `failed` regardless of evidence.
    """
    _drive_one_cycle(_autonomous(tmp_path), QUESTION)

    banked = _episodes(tmp_path)[0]
    assert banked.outcome in {"success", "partial"}, (
        f"a completed cycle was scored {banked.outcome!r}"
    )
    assert "aborted" not in banked.tags
