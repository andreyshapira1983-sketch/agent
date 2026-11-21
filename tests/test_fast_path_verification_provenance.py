"""Fail-before tests for the episodic fast-path safety fix (Variant 2+3).

Target semantics (operator-approved, see docs/audit/MASTER_ISSUE_REGISTRY.md):
the episodic fast-path may replay a stored answer ONLY when the *source* episode
has an explicit `verification_status == verified` (never inferred from
`answer_quality_score`, `outcome`, `tools_used`, `source_labels`, or old
`verified_chunks`), the record is NOT a `memory_replay`, and the existing safe
constraints hold. A replay must be stored with `response_origin=memory_replay`,
`verification_status=not_run`, `verified_chunks=0`, must create no new evidence,
and must not become a fast-path candidate itself.

Two independent defects share this execution path:
  * MIR-002 — an ungrounded / verifier-error answer scores `quality=1.0` and, on
    current code, is replay-eligible because the gate keys on `quality >= 0.70`.
  * MIR-041 — a replay is re-banked with `verified_chunks=1` and is itself
    fast-path-eligible → a self-reinforcing "verified success" chain.

CURRENT-CODE EXPECTATION (fail-before): the fast-path gate is
`answer_quality_score >= 0.70 and not tools_used` (core/loop.py:757-796), so every
quality-1.0 episode here triggers it and every replay is re-banked with
`verified_chunks=1`. Tests 1-6 therefore FAIL on current code (that is the point);
test 7 (protective) PASSES and must keep passing after the fix.

These tests are behavioural (they drive AgentLoop.run and observe whether the
`episodic_fast_path` event fired / what was banked), NOT assertions on fields that
do not exist yet — so each fails for the intended reason, not an AttributeError.
"""
from __future__ import annotations

from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.smart_memory import (
    EpisodicMemoryStore,
    episode_from_agent_cycle,
)
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_Q = "what is the capital of Australia"


def _make_loop(workspace: Path) -> tuple[AgentLoop, list[str], EpisodicMemoryStore]:
    """Build an AgentLoop wired to a real episodic store, capturing log event types.

    The real TraceLogger is kept (all its methods work); only `.log` is wrapped so
    we can see whether `episodic_fast_path` fired.
    """
    registry = ToolRegistry()
    llm = FakeLLM(responses=[])
    store = EpisodicMemoryStore(workspace / "data" / "episodes.jsonl")
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[], reasoning="no tools needed"),
        episodic_store=store,
    )
    events: list[str] = []
    _orig = loop.log.log

    def _spy(event_type, *a, **k):
        events.append(event_type)
        return _orig(event_type, *a, **k)

    loop.log.log = _spy  # type: ignore[method-assign]
    return loop, events, store


def _fast_path_fired(events: list[str]) -> bool:
    return "episodic_fast_path" in events


# ─────────────────────────────── MIR-002 ────────────────────────────────────

def test_mir002_1_ungrounded_quality1_episode_must_not_fast_path(workspace: Path):
    """(1) An evidence-free answer is not replayed even when admitted and declared achieved.

    Seeded as admitted so the quality gate, not the eligibility gate, must refuse it.
    """
    loop, events, store = _make_loop(workspace)
    ungrounded = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Sydney.",
        tools_used=[], source_labels=[],
        verified_chunks=0, unverified_chunks=0,  # empty chain: nothing was measured
        usage_eligible=True,
        declared_completion="achieved",
    )
    store.save(ungrounded)

    loop.run(_Q)

    assert not _fast_path_fired(events), (
        "fast-path replayed an answer with an empty evidence chain — an unmeasured "
        "answer must not count as quality 1.0"
    )


# ─────────────────────────────── MIR-041 ────────────────────────────────────


def test_mir041_5_replay_episode_must_not_be_fast_path_candidate(workspace: Path):
    """(5) A replay-shaped record (`memory:<id>` source) is refused even when it claims success.

    Shaped as the MIR-041 defect banked replays (verified chunk, achieved), so the
    memory-provenance rule of admission is the only gate left to refuse it.
    """
    loop, events, store = _make_loop(workspace)
    replay_shaped = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Sydney.",
        tools_used=[], source_labels=["memory:ep_prev"],  # a prior replay
        verified_chunks=1, unverified_chunks=0,
        declared_completion="achieved",  # usage_eligible left to admission
    )
    store.save(replay_shaped)

    loop.run(_Q)

    assert not _fast_path_fired(events), (
        "fast-path used a memory_replay record as its source — replays must never "
        "be fast-path candidates"
    )
