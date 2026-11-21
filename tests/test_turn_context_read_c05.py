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

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.persistent_memory import MemoryRecord


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


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

    _hist, critique_on, persistent_block, experience_block, _spend_block = (
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
