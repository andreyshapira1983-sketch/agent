"""C05 — what the turn-context read actually touches, and what it suppresses.

Two obligations of the cluster, both measured before they were written:

1. THE "READ-ONLY" CLAIM IS FALSE AS WRITTEN. `core/loop_context.py` opens with
   «Чтение и только чтение — ни один из этих шагов ничего не меняет за
   пределами журнала и полей на цикле», and one of the enumerated steps is the
   long-term retrieval. That retrieval bumps `access_count` /
   `last_accessed_at` on every selected record and rewrites the persistent
   store (`core/loop_memory_read.py:152-170`). The write is deliberate — the
   archive scorer needs to know which records earn their keep — and it is
   gated by the `access_stats` durable sink. So the honest contract is not
   "read-only" but "reads, plus exactly one durable side effect, and that one
   obeys its permission". Both poles are pinned here, so a second durable sink
   appearing in this phase reds immediately.

2. LOCAL-CRITIQUE SUPPRESSION HAD NO BITE. Disabling the suppression branch
   (loop_context.py:149) left 33 tests across the critique, memory-wiring and
   persistent suites green — measured 2026-08-08 (M35). The PR2 contract is
   that a critique turn analyses the named referent, not the agent's memories;
   without a bite, that contract lived only in a comment.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.bootstrap import DEFAULT_PERSISTENT_PATH, build_agent
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.persistent_memory import MemoryRecord

_SINKS = (
    Path("data") / "persistent_memory.jsonl",
    Path("data") / "episodic_memory.jsonl",
    Path("data") / "procedural_memory.jsonl",
    Path("data") / "memory_consolidation.jsonl",
    Path("data") / "source_registry.jsonl",
    Path("data") / "user_profile.jsonl",
    Path("data") / "assumptions.jsonl",
)

QUESTION = "что мы знаем про политику памяти проекта"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _snapshot(workspace: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for rel in _SINKS:
        target = workspace / rel
        snap[str(rel)] = (
            hashlib.sha256(target.read_bytes()).hexdigest()
            if target.exists()
            else "<absent>"
        )
    return snap


def _seed_persistent(agent: AgentLoop) -> MemoryRecord:
    """One retrievable record that overlaps the question's keywords."""
    record = MemoryRecord(
        content="Политика памяти проекта: записи хранятся в data/persistent_memory.jsonl",
        source="user-explicit",
        tags=["project", "память", "политика"],
    )
    agent.persistent_store.save(record)
    return record


def _agent(workspace: Path, **kwargs) -> AgentLoop:
    agent = build_agent(
        workspace, with_memory=True, approval_provider=None, **kwargs
    )
    agent.memory = WorkingMemory()
    return agent


# ── 1. The real contract of the read: one durable side effect, permission-gated ──

def test_the_turn_context_read_touches_only_the_access_stats_sink(
    tmp_path: Path,
) -> None:
    """Not read-only: it bumps access stats — and NOTHING else may change."""
    agent = _agent(tmp_path)
    _seed_persistent(agent)
    before = _snapshot(tmp_path)

    agent._retrieve_turn_context(QUESTION, file_hint=None)

    after = _snapshot(tmp_path)
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {str(DEFAULT_PERSISTENT_PATH)}, (
        "the turn-context read must touch exactly one durable sink — the "
        f"access-stats bump on persistent records; changed: {sorted(changed)}"
    )


def test_the_access_bump_is_the_thing_that_changed_it(tmp_path: Path) -> None:
    """Name the write, so 'the file changed' cannot hide a different write."""
    agent = _agent(tmp_path)
    _seed_persistent(agent)
    before = agent.persistent_store.load()[0].access_count

    agent._retrieve_turn_context(QUESTION, file_hint=None)

    after = agent.persistent_store.load()[0].access_count
    assert after == before + 1, (
        f"access_count did not advance ({before} -> {after}): either the "
        "record was not retrieved, or the bump moved elsewhere"
    )


def test_suppressing_the_access_stats_sink_makes_the_read_truly_read_only(
    tmp_path: Path,
) -> None:
    """The second pole: with the sink withheld, nothing on disk moves at all."""
    agent = _agent(tmp_path, durable_writes=frozenset({"episode"}))
    _seed_persistent(agent)
    before = _snapshot(tmp_path)

    block = agent._retrieve_turn_context(QUESTION, file_hint=None)[2]

    assert block.strip(), "retrieval itself must still work when the sink is off"
    assert _snapshot(tmp_path) == before, (
        "with `access_stats` off the durable state must be untouched — the "
        "permission gate on this write is what makes the phase auditable"
    )


# ── 2. Local-critique suppression (M35 hole) ──────────────────────────────────

def test_a_local_critique_turn_withholds_memory_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR2's contract, bitten at last: a critique turn analyses the referent.

    With the referent resolver ON and a critique-eligible turn, long-term and
    experience blocks must be withheld — mixing memories into "critique THIS
    text" analyses the wrong thing. Removing the suppression branch left 33
    tests green (M35), so this is the only observer of that contract.
    """
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "on")
    agent = _agent(tmp_path)
    _seed_persistent(agent)

    # A prior turn gives the resolver something to point at.
    agent.memory.record_turn(
        question="покажи политику памяти проекта",
        planner_reasoning="",
        tools_used=[],
        artifact_labels=[],
        answer="Политика памяти проекта: записи в data/persistent_memory.jsonl",
    )

    # The question must overlap the seeded record's keywords, or this test is
    # vacuous — the first draft asked «разбери этот ответ», for which retrieval
    # returns nothing anyway, so `persistent_block == ""` held with the
    # suppression disabled too (caught by the break-did-not-redden rule).
    critique_question = "разбери этот ответ про политику памяти проекта"
    assert agent._retrieve_persistent(critique_question).strip(), (
        "precondition: without suppression this question DOES retrieve — "
        "otherwise the assertions below prove nothing"
    )

    _hist, critique_on, persistent_block, experience_block = (
        agent._retrieve_turn_context(critique_question, file_hint=None)
    )

    assert critique_on, (
        "precondition: the turn must be critique-eligible, otherwise this "
        "test proves nothing about the suppression"
    )
    assert persistent_block == "", (
        "long-term memory leaked into a local-critique turn (M35 hole)"
    )
    assert experience_block == "", (
        "experience memory leaked into a local-critique turn (M35 hole)"
    )


def test_an_ordinary_turn_still_receives_the_memory_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other pole: suppression must be conditional, not a permanent off."""
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "on")
    agent = _agent(tmp_path)
    _seed_persistent(agent)

    _hist, critique_on, persistent_block, _experience = (
        agent._retrieve_turn_context(QUESTION, file_hint=None)
    )

    assert not critique_on
    assert persistent_block.strip(), (
        "an ordinary turn must still receive long-term memory — a suppression "
        "that never lifts is not a gate"
    )
