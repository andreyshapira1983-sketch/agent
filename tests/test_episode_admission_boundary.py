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
from core.self_build_memory import recent_self_build_lessons, record_self_build_episode
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
)
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
