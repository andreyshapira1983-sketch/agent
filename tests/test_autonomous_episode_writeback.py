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
from tests.conftest import FakeLLM
from tests.test_memory_core_wiring import (
    SYNTH_GENERAL,
)

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
