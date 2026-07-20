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

import threading
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_EPISODIC_MEMORY_PATH, build_agent
from core.loop import AgentLoop
from core.run_context import RunContext, current_run, run_scope
from core.smart_memory import (
    EpisodicMemoryStore,
    episode_from_agent_cycle,
    episode_id_for_run,
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


# ==========================================================================
# 1. RunContext lifecycle
# ==========================================================================
def test_context_is_empty_outside_a_run() -> None:
    assert current_run() is None


def test_context_is_restored_after_normal_exit() -> None:
    with run_scope("run-a"):
        assert current_run() == RunContext(run_id="run-a", task_id=None)
    assert current_run() is None


def test_context_is_restored_after_an_exception() -> None:
    with pytest.raises(RuntimeError):
        with run_scope("run-a", task_id="T-1"):
            assert current_run() is not None
            raise RuntimeError("boom")
    assert current_run() is None, "the scope must unwind on the error path too"


def test_nested_scopes_restore_the_outer_context() -> None:
    """reset(token) must restore, not merely clear."""
    with run_scope("outer", task_id="T-outer"):
        with run_scope("inner", task_id="T-inner"):
            assert current_run().run_id == "inner"
        ctx = current_run()
        assert ctx is not None and ctx.run_id == "outer", (
            "leaving the inner scope must restore the outer one, not None"
        )
    assert current_run() is None


def test_overlapping_runs_do_not_share_context() -> None:
    """Two concurrent scopes must each see their own run identity."""
    seen: dict[str, str | None] = {}
    both_inside = threading.Barrier(2, timeout=5)

    def worker(name: str) -> None:
        with run_scope(f"run-{name}", task_id=f"T-{name}"):
            both_inside.wait()          # hold the scope open while the other enters
            ctx = current_run()
            seen[name] = ctx.run_id if ctx else None

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert seen == {"a": "run-a", "b": "run-b"}, (
        f"overlapping runs clobbered each other's identity: {seen}"
    )


# ==========================================================================
# 2. task_id vs run_id on the episode
# ==========================================================================
def test_episode_carries_run_id_and_task_id(tmp_path: Path) -> None:
    agent = _interactive(tmp_path)
    _drive_one_cycle(agent, "how much is two plus two", task_id="T-42")

    banked = _episodes(tmp_path)
    assert len(banked) == 1
    assert banked[0].run_id, "episode must record which attempt produced it"
    assert banked[0].task_id == "T-42"


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


def test_episode_id_is_derived_from_run_id() -> None:
    ep = _episode_for("run-abc")
    assert ep.id == episode_id_for_run("run-abc"), (
        "a deterministic id is what makes the duplicate check possible"
    )


def test_save_once_writes_the_first_time(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl")
    assert store.save_once(_episode_for("run-1")) is True
    assert len(store.load()) == 1


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


def test_save_once_admits_a_different_run(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl")
    store.save_once(_episode_for("run-1"))
    assert store.save_once(_episode_for("run-2")) is True
    assert len(store.load()) == 2


def test_idempotency_is_bounded_by_the_fifo_window(tmp_path: Path) -> None:
    """Names the limit instead of implying a permanent guarantee.

    Once an episode is evicted from the FIFO window, its run_id is writable
    again. That is acceptable for in-run deduplication and is why this is
    called *bounded* idempotency -- no separate ledger is kept.
    """
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=2)
    store.save_once(_episode_for("run-1"))
    store.save_once(_episode_for("run-2"))
    store.save_once(_episode_for("run-3"))          # evicts run-1
    assert "run-1" not in {e.run_id for e in store.load()}

    # run-1 is outside the window now, so its id is writable again. The
    # duplicate check is a window, not a permanent claim.
    assert store.save_once(_episode_for("run-1")) is True
    assert "run-1" in {e.run_id for e in store.load()}
    # ...and re-admitting it evicts the next-oldest, keeping the window bounded.
    assert len(store.load()) == store.max_episodes
