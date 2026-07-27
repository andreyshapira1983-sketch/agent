"""The admission policy runs at the store boundary, not at one call site.

`decide_usage_eligibility` used to be applied by exactly one of the three write
paths. The cycle applied it; `core/self_repair.py` (repair lessons) and
`core/self_build_memory.py` (self-build / self-apply outcomes) called `save()`
straight through, so their records landed with `usage_eligible=None`. Every
reader is fail-closed on `None`, which made the three `lesson`-specific branches
in `_retrieve_experience_memory` dead for exactly the records they exist to
surface — records the policy itself would have admitted, since the `lesson` tag
is its first rule.

Measured before the fix, on a repair-shaped lesson: the store found it by name,
by tag, and at `find_most_similar` score 1.0, while the planner-facing reader
returned 0 characters.

The boundary is now the decider. A caller may still decide explicitly — that is
how an aborted run stays quarantined — but it can no longer decline to decide.
"""
from __future__ import annotations

from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.self_build_memory import record_self_build_episode, recent_self_build_lessons
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    admit_for_storage,
    decide_usage_eligibility,
    episode_from_agent_cycle,
    is_usage_eligible,
)
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked
from tools.base import ToolRegistry


def _repair_lesson() -> EpisodeRecord:
    """Exactly what core/self_repair.py builds after a successful repair."""
    return EpisodeRecord(
        goal="repair",
        question="fix core/foo.py",
        outcome="success",
        summary="Bug fixed in 'core/foo.py': off-by-one in the retry cap.",
        tools_used=("shell_exec", "run_tests"),
        tags=("lesson", "bug-fix", "regression-guard"),
    )


def _loop(workspace: Path, store: EpisodicMemoryStore) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=None,
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
        ),
        episodic_store=store,
    )


# ── the boundary decides ─────────────────────────────────────────────────────

def test_direct_save_admits_a_repair_lesson(workspace: Path):
    store = EpisodicMemoryStore(workspace / "ep.jsonl")

    store.save(_repair_lesson())

    stored = store.load()[0]
    assert stored.usage_eligible is True
    assert is_usage_eligible(stored)


def test_repair_lesson_reaches_the_planner(workspace: Path):
    """The defect itself: found by the store, invisible to the reader."""
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    store.save(_repair_lesson())
    loop = _loop(workspace, store)

    block = loop._retrieve_experience_memory("fix core/foo.py")

    assert block, "the lesson never reached the experience block"
    assert "off-by-one" in block


def test_self_build_lesson_reaches_the_planner(workspace: Path):
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    loop = _loop(workspace, store)
    record_self_build_episode(
        loop,
        kind="self-build-produce",
        result={
            "status": "critic_veto",
            "reason": "dangling import to a moved class",
            "target_path": "core/foo.py",
            "veto_reasons": ["import not updated"],
        },
    )

    assert store.load()[0].usage_eligible is True
    block = loop._retrieve_experience_memory(
        "produce self-build patch for core/foo.py"
    )
    assert "dangling import" in block
    # The self-build lane's own ungated reader keeps working unchanged.
    assert recent_self_build_lessons(loop, "core/foo.py")


def test_save_once_admits_too(workspace: Path):
    store = EpisodicMemoryStore(workspace / "ep.jsonl")

    assert store.save_once(_repair_lesson()) is True

    assert store.load()[0].usage_eligible is True


def test_save_returns_the_admitted_record(workspace: Path):
    store = EpisodicMemoryStore(workspace / "ep.jsonl")

    written = store.save(_repair_lesson())

    assert written.usage_eligible is True
    assert written.id == store.load()[0].id


# ── the policy actually runs — it does not just stamp True ───────────────────

def test_an_unevidenced_episode_is_still_refused(workspace: Path):
    """Admission is a decision, not a rubber stamp."""
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    store.save(
        episode_from_agent_cycle(
            goal="chat",
            question="как дела",
            answer="Conclusion: хорошо [general-knowledge]",
            tools_used=[],
            source_labels=["general-knowledge"],
            verified_chunks=0,
            unverified_chunks=0,
        )
    )

    stored = store.load()[0]
    assert stored.usage_eligible is False
    assert not is_usage_eligible(stored)


# ── an explicit decision is a decision, and is honoured ──────────────────────

def test_explicit_false_survives_the_boundary(workspace: Path):
    """An aborted run is quarantined by its writer and must stay quarantined."""
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    lesson = _repair_lesson()
    quarantined = EpisodeRecord(
        **{**{f: getattr(lesson, f) for f in lesson.__dataclass_fields__},
           "usage_eligible": False}
    )

    store.save(quarantined)

    assert store.load()[0].usage_eligible is False


def test_admit_for_storage_is_idempotent():
    once = admit_for_storage(_repair_lesson())
    twice = admit_for_storage(once)
    assert once == twice
    assert once.usage_eligible is decide_usage_eligibility(_repair_lesson())


# ── admitting more records must not open the replay door ────────────────────

def test_an_admitted_lesson_is_still_not_replayable(workspace: Path):
    """Readable as experience is not the same as servable as an answer.

    Admission is what the planner's experience block reads. The fast path is a
    different question — it returns a stored answer verbatim instead of running
    a cycle — and a lesson clears neither of its other gates: no frozen
    completion state, and no measured quality.
    """
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    lesson = store.save(_repair_lesson())

    assert lesson.usage_eligible is True
    assert not AgentLoop._fast_path_allows_replay(lesson, 1.0)
    assert not AgentLoop._quality_allows_replay(lesson)


# ── history is not rewritten ─────────────────────────────────────────────────

def test_a_legacy_row_keeps_its_unclassified_state(workspace: Path):
    """`None` on disk means "written before the field existed".

    Admission is a write-time decision: re-deciding an old row on read would
    reclassify it under today's rule and destroy the distinction the field was
    given (`None` = never classified, `False` = examined and refused).
    """
    path = workspace / "ep.jsonl"
    store = EpisodicMemoryStore(path)
    store.save(_repair_lesson())
    rows = read_state_jsonl_unlocked(path)
    rows[0].pop("usage_eligible", None)  # the shape of a pre-field row
    rewrite_state_jsonl_unlocked(path, rows)

    stored = store.load()[0]

    assert stored.usage_eligible is None
    assert not is_usage_eligible(stored)


def test_pruning_does_not_admit_legacy_rows(workspace: Path):
    """Eviction rewrites the file; it must not launder old rows into admitted."""
    path = workspace / "ep.jsonl"
    store = EpisodicMemoryStore(path, max_episodes=2)
    store.save(_repair_lesson())
    rows = read_state_jsonl_unlocked(path)
    rows[0].pop("usage_eligible", None)
    rewrite_state_jsonl_unlocked(path, rows)

    for i in range(3):  # push past max_episodes so a prune happens
        store.save(
            episode_from_agent_cycle(
                goal="g", question=f"q{i}", answer="a",
                tools_used=[], source_labels=[], run_id=f"r{i}",
            )
        )

    legacy = [e for e in store.load() if "lesson" in e.tags]
    assert legacy, "the protected lesson was evicted"
    assert legacy[0].usage_eligible is None
