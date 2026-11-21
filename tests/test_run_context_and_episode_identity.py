"""Sub-step 2b — run identity: RunContext, episode task_id/run_id, save_once.

Three things this pins, all of which are prerequisites for autonomous episode
write-back (2d) but none of which enable it:

1. **RunContext via ContextVar.** Run identity must not live in a mutable
   `AgentLoop` field: two overlapping runs of one instance would clobber each
   other. Today `api/server.py` serialises `agent.run()` behind `_run_lock`, so
   overlap cannot happen *at present* — but that is one lock away from being
   wrong, and a ContextVar is correct without depending on it.

2. **task_id vs run_id.** `task_id` names the logical task and survives a
   retry; `run_id` names one attempt and is always fresh. A retried task
   therefore banks several episodes sharing a task_id. Crucially, `trace_id`
   cannot serve as run_id: it is created once per agent in `build_agent`, so
   every task drained by one autonomous agent would share it.

3. **Bounded idempotency.** The episode id is derived from run_id, and the
   check-and-write happens inside the store under one lock. "Bounded" is
   literal: the guarantee holds while the episode is inside the FIFO window
   (`max_episodes`), since no separate ledger is kept.

Status when written: every test here FAILS -- `core.run_context` does not
exist, `EpisodeRecord` has no task_id/run_id, and `save_once` is not a method.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import (
    DEFAULT_EPISODIC_MEMORY_PATH,
    build_agent,
)
from core.loop import AgentLoop
from core.smart_memory import (
    EpisodicMemoryStore,
    episode_from_agent_cycle,
)
from tests.test_memory_core_wiring import _drive_one_cycle


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _interactive(workspace: Path) -> AgentLoop:
    """The profile that writes every sink -- 2b builds the mechanism, 2d turns
    it on for the unattended profile."""
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _episodes(workspace: Path):
    return EpisodicMemoryStore(workspace / DEFAULT_EPISODIC_MEMORY_PATH).load()


def test_two_runs_of_one_agent_get_distinct_run_ids(tmp_path: Path) -> None:
    """Regression against using trace_id as run_id.

    trace_id is created once per agent in build_agent, so if it were the run
    identity every task drained by one autonomous agent would collide -- and
    idempotency would silently drop all episodes after the first.
    """
    agent = _interactive(tmp_path)
    _drive_one_cycle(agent, "first question")
    _drive_one_cycle(agent, "second question")

    banked = _episodes(tmp_path)
    assert len(banked) == 2, "both cycles must bank an episode"
    assert banked[0].run_id != banked[1].run_id


def test_retry_keeps_task_id_and_takes_a_fresh_run_id(tmp_path: Path) -> None:
    agent = _interactive(tmp_path)
    _drive_one_cycle(agent, "flaky task", task_id="T-7")
    _drive_one_cycle(agent, "flaky task", task_id="T-7")   # the retry

    banked = _episodes(tmp_path)
    assert len(banked) == 2, "a retry is a separate attempt and banks its own episode"
    assert {e.task_id for e in banked} == {"T-7"}
    assert banked[0].run_id != banked[1].run_id, (
        "idempotency keys on run_id, so a retry must not be suppressed"
    )


# ==========================================================================
# 3. Bounded idempotency, enforced inside the store
# ==========================================================================
def _episode_for(run_id: str, question: str = "q"):
    return episode_from_agent_cycle(
        goal="g",
        question=question,
        answer="a",
        tools_used=[],
        source_labels=["general-knowledge"],
        run_id=run_id,
        task_id="T-1",
    )


def test_save_once_rejects_a_duplicate_from_a_fresh_store_instance(tmp_path: Path) -> None:
    """Idempotency must be durable, not in-memory bookkeeping.

    A second store object over the same file -- which is what a restarted
    process has -- must still refuse the duplicate.
    """
    path = tmp_path / "ep.jsonl"
    EpisodicMemoryStore(path).save_once(_episode_for("run-1"))

    reopened = EpisodicMemoryStore(path)
    assert reopened.save_once(_episode_for("run-1", question="different text")) is False
    assert len(reopened.load()) == 1, "the duplicate must not be appended"
