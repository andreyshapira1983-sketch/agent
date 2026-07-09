"""TD-019: the knowledge-pipeline write path must not reload the persistent
store once per claim.

On a repeated read-only turn (e.g. a work-session re-running the same goal) the
pipeline re-extracts the same ~60 claims from the same files every cycle and
attempts a persistent-memory write for each. Previously every ``remember`` call
reloaded the whole store to run the dedup gate — an O(claims × records) disk
reload per cycle whose writes are almost all rejected as duplicates. The batch
remember callback loads the snapshot ONCE per pass while preserving dedup/echo
behavior exactly.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import FakeLLM
from tests.test_persistent_integration import _build_agent


def _spy_load(store):
    """Wrap store.load to count invocations; returns a mutable counter dict."""
    counter = {"loads": 0}
    original = store.load

    def counting_load(*args, **kwargs):
        counter["loads"] += 1
        return original(*args, **kwargs)

    store.load = counting_load  # type: ignore[method-assign]
    return counter


def test_remember_uses_passed_snapshot_without_reloading(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    agent, store, _ = _build_agent(workspace, FakeLLM(), path)
    counter = _spy_load(store)

    snapshot: list = []
    decision, record = agent.remember(
        content="The router selects a provider by complexity tier.",
        tags=["fact", "knowledge"],
        source="agent-auto",
        record_type="semantic",
        owner="self",
        existing=snapshot,
    )

    assert decision.decision == "save"
    assert record is not None
    # No reload happened — the caller-supplied snapshot was used.
    assert counter["loads"] == 0
    # The saved record was appended so later writes in the pass can dedup on it.
    assert record in snapshot


def test_knowledge_remember_batch_loads_store_once(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    agent, store, _ = _build_agent(workspace, FakeLLM(), path)
    counter = _spy_load(store)

    remember = agent._knowledge_remember_batch()
    # One load happens when the batch is created, none per write.
    assert counter["loads"] == 1

    contents = [
        "Alpha module owns evidence ranking.",
        "Bravo module owns conflict resolution.",
        "Charlie module owns source persistence.",
        "Delta module owns claim extraction.",
    ]
    saved = 0
    for text in contents:
        decision, record = remember(text, ["fact", "knowledge"], "agent-auto", "semantic", "self")
        if decision.decision == "save":
            saved += 1

    assert saved == len(contents)
    # Still exactly one store load for the whole batch — not one per write.
    assert counter["loads"] == 1


def test_knowledge_remember_batch_still_dedups_within_pass(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    agent, store, _ = _build_agent(workspace, FakeLLM(), path)

    remember = agent._knowledge_remember_batch()
    text = "The deep escalation gate downgrades unapproved deep requests."

    first_decision, first_record = remember(text, ["fact", "knowledge"], "agent-auto", "semantic", "self")
    second_decision, second_record = remember(text, ["fact", "knowledge"], "agent-auto", "semantic", "self")

    assert first_decision.decision == "save"
    assert first_record is not None
    # Identical content in the same pass is still rejected as a duplicate,
    # proving the snapshot stayed current without a reload.
    assert second_decision.decision == "reject"
    assert second_record is None
    assert any("duplicate" in r.lower() for r in second_decision.reasons)


def test_knowledge_remember_batch_dedups_against_preexisting_store(workspace: Path):
    """The dominant cross-cycle case: content already in the store is rejected
    on the next pass without a per-claim reload."""
    path = workspace / "data" / "mem.jsonl"
    agent, store, _ = _build_agent(workspace, FakeLLM(), path)

    text = "Adaptive routing chooses the planner and synthesizer models."
    # Cycle 1 pass: content is new → saved.
    remember1 = agent._knowledge_remember_batch()
    d1, r1 = remember1(text, ["fact", "knowledge"], "agent-auto", "semantic", "self")
    assert d1.decision == "save" and r1 is not None

    # Cycle 2 pass: fresh batch reloads once; the same content is now a dup.
    counter = _spy_load(store)
    remember2 = agent._knowledge_remember_batch()
    assert counter["loads"] == 1
    d2, r2 = remember2(text, ["fact", "knowledge"], "agent-auto", "semantic", "self")
    assert d2.decision == "reject"
    assert r2 is None
    # A whole second cycle of identical claims would add no further reloads.
    assert counter["loads"] == 1
