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

from core.loop import AgentLoop, new_trace_id
from core.logger import TraceLogger
from core.policy import PolicyGate
from core.smart_memory import (
    EpisodicMemoryStore,
    EpisodeRecord,
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


def _replay_episodes(store: EpisodicMemoryStore) -> list[EpisodeRecord]:
    """Episodes banked by a fast-path replay carry a `memory:<id>` source label."""
    return [
        ep
        for ep in store.load()
        if any(str(lbl).startswith("memory:") for lbl in ep.source_labels)
    ]


# ─────────────────────────────── MIR-002 ────────────────────────────────────

def test_mir002_1_ungrounded_quality1_episode_must_not_fast_path(workspace: Path):
    """(1) An UNVERIFIED answer with quality=1.0 (empty evidence chain) must NOT
    be replayed by the fast-path. On current code it is (gate = quality>=0.70)."""
    loop, events, store = _make_loop(workspace)
    ungrounded = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Sydney.",
        tools_used=[], source_labels=[],
        verified_chunks=0, unverified_chunks=0,  # -> quality 1.0, outcome success
    )
    store.save(ungrounded)

    loop.run(_Q)

    assert not _fast_path_fired(events), (
        "fast-path replayed an UNVERIFIED (quality=1.0) answer — it must require "
        "an explicit verification_status==verified"
    )


def test_mir002_2_verifier_error_episode_must_not_fast_path(workspace: Path):
    """(2) An episode produced when the verifier itself threw (soft-fail:
    verified=0/unverified=0/fully_unverified=False → quality 1.0) must NOT be
    treated as verified and must NOT fast-path."""
    loop, events, store = _make_loop(workspace)
    verifier_errored = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Sydney.",
        tools_used=[], source_labels=[],
        verified_chunks=0, unverified_chunks=0,  # the soft-fail shape (loop.py:1533)
    )
    store.save(verifier_errored)

    loop.run(_Q)

    assert not _fast_path_fired(events), (
        "fast-path replayed an answer whose verification never ran (verifier "
        "error) — verifier_error must not count as verified"
    )


def test_mir002_3_legacy_episode_without_provenance_must_not_fast_path(workspace: Path):
    """(3) An OLD episode deserialized without an explicit provenance field must
    default to not-verified (not_run) and must NOT fast-path — no guessing
    verification from legacy fields."""
    loop, events, store = _make_loop(workspace)
    legacy = EpisodeRecord.from_dict({
        "goal": "answer",
        "question": _Q,
        "outcome": "success",
        "summary": "Sydney.",
        "full_answer": "Sydney.",
        "tools_used": [],
        "source_labels": [],
        "verified_chunks": 1,   # a legacy count — must NOT be read as "verified"
        "unverified_chunks": 0,
        # NOTE: no verification_status / response_origin key (pre-fix record)
    })
    store.save(legacy)

    loop.run(_Q)

    assert not _fast_path_fired(events), (
        "fast-path replayed a legacy episode with no explicit verification "
        "provenance — legacy records must be treated as not_run"
    )


# ─────────────────────────────── MIR-041 ────────────────────────────────────

def test_mir041_4_replay_must_not_be_recorded_verified(workspace: Path):
    """(4) When a fast-path replay happens, the replay episode it banks must NOT
    carry verified_chunks=1 (a replay produces no new evidence)."""
    loop, events, store = _make_loop(workspace)
    source = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Canberra.",
        tools_used=[], source_labels=["file:atlas.txt"],
        verified_chunks=3, unverified_chunks=0,  # a genuinely-evidenced answer
        # Eligibility (2c) fail-closes on unclassified episodes, so the seed
        # must be explicitly admitted for the fast path to fire at all —
        # otherwise this test never reaches the assertion it exists for.
        usage_eligible=True,
    )
    store.save(source)

    loop.run(_Q)

    replays = _replay_episodes(store)
    assert replays, "expected a replay episode to be banked (fast-path should have fired)"
    assert all(r.verified_chunks == 0 for r in replays), (
        "a memory replay was re-banked with verified_chunks>=1 — a replay must "
        "create no new verified evidence"
    )


def test_mir041_5_replay_episode_must_not_be_fast_path_candidate(workspace: Path):
    """(5) A replay-banked episode must not itself become a fast-path source. A
    record shaped like a replay (a `memory:<id>` source label, no tools) must be
    rejected by the gate."""
    loop, events, store = _make_loop(workspace)
    replay_shaped = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Sydney.",
        tools_used=[], source_labels=["memory:ep_prev"],  # a prior replay
        verified_chunks=1, unverified_chunks=0,
    )
    store.save(replay_shaped)

    loop.run(_Q)

    assert not _fast_path_fired(events), (
        "fast-path used a memory_replay record as its source — replays must never "
        "be fast-path candidates"
    )


def test_mir041_6_repeated_asks_must_not_self_reinforce_verified_chain(workspace: Path):
    """(6) Repeated near-identical asks must not grow a self-reinforcing chain of
    verified-success episodes. Only the one genuinely-evidenced source may remain
    a 'verified' record."""
    loop, events, store = _make_loop(workspace)
    source = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Canberra.",
        tools_used=[], source_labels=["file:atlas.txt"],
        verified_chunks=3, unverified_chunks=0,
    )
    store.save(source)

    for _ in range(3):
        loop.run(_Q)

    verified_success = [
        ep for ep in store.load()
        if ep.outcome == "success" and ep.verified_chunks >= 1
    ]
    assert len(verified_success) == 1, (
        f"self-reinforcing loop: {len(verified_success)} verified-success episodes "
        "after 3 asks — replays must not mint new verified-success records"
    )


# ─────────────────────────── protective (guard) ─────────────────────────────

def test_mir_protective_7_genuinely_verified_episode_may_fast_path(workspace: Path):
    """(7) GUARD: a genuinely-verified source episode must still be allowed to use
    the safe fast-path, so the fix does not destroy the legitimate optimization.

    Passes on current code (gate = quality>=0.70). After the fix, the seeded
    episode must be created with verification_status==verified (a field the fix
    adds) for this to keep passing — update the seeding when the field lands.
    """
    loop, events, store = _make_loop(workspace)
    verified = episode_from_agent_cycle(
        goal="answer", question=_Q, answer="Canberra.",
        tools_used=[], source_labels=["file:atlas.txt"],
        verified_chunks=3, unverified_chunks=0,
        # Seeding updated exactly as this docstring instructs. The eligibility
        # field (2c) has landed and fail-closes on unclassified episodes, so a
        # genuinely-verified seed must now say so explicitly.
        usage_eligible=True,
    )
    store.save(verified)

    loop.run(_Q)

    assert _fast_path_fired(events), (
        "a genuinely-verified answer was NOT fast-pathed — the safe optimization "
        "must survive the fix"
    )
