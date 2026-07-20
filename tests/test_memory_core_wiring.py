"""Wiring test: is memory actually connected to the core on EVERY production path?

The existing memory suites construct `AgentLoop` **by hand**, passing every
store in explicitly. That proves the memory *mechanisms* work, but it silently
bypasses `build_agent` — the single place that decides whether a given
production path gets memory **at all**. Nothing in `tests/` calls `build_agent`,
which is exactly why this gap survived: in tests every path looks memory-rich.

Phase 1 target (core <-> memory connection, MIR-043): the unattended agent
participates in experience learning like the interactive one.

Today it does not. `app/bootstrap.py:151` nests `if with_memory:` inside
`if with_persistent:`, tying episodic/procedural/consolidation memory to the
*working-memory* flag, and every autonomous entry point calls
`build_agent(..., with_memory=False)` (`agent_tick.py:645/806/1121`). So the
agent that runs alone records no episodes, builds no procedures, and cannot
read the experience it accumulated while a human was driving.

Status when written: tests 2 and 3 FAIL on current code (that is their purpose).
Tests 1 and 4 are GUARDS — they pass now and must keep passing after the fix.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_tick import UNATTENDED_MEMORY_PROFILE
from app.bootstrap import (
    DEFAULT_ASSUMPTIONS_PATH,
    DEFAULT_EPISODIC_MEMORY_PATH,
    DEFAULT_MEMORY_CONSOLIDATION_PATH,
    DEFAULT_PERSISTENT_PATH,
    DEFAULT_PROCEDURAL_MEMORY_PATH,
    DEFAULT_SOURCE_REGISTRY_PATH,
    DEFAULT_USER_PROFILE_PATH,
    build_agent,
)
from core.loop import AgentLoop
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from tests.conftest import FakeLLM, FakePlanner


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build agents without provider credentials and without network calls."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _interactive(workspace: Path) -> AgentLoop:
    """The REPL profile — `main.py:2079` (and `api/server.py:84`)."""
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _autonomous(workspace: Path) -> AgentLoop:
    """The unattended profile, exactly as `agent_tick.py` builds it.

    The profile is imported rather than restated so that changing the
    production profile breaks these tests instead of silently diverging.
    """
    return build_agent(
        workspace, approval_provider=None, **UNATTENDED_MEMORY_PROFILE
    )


def _one_shot(workspace: Path) -> AgentLoop:
    """The `--ask` profile — `main.py:2015`, deliberately isolated."""
    return build_agent(
        workspace, with_memory=False, with_persistent=False, approval_provider=None
    )


SYNTH_GENERAL = """Conclusion:
Four.

Facts:
- two plus two is four [general-knowledge]

Sources:
1. general-knowledge - general-knowledge

Confidence: medium

Unverified: nothing

Safety: nothing"""

# Every durable sink the loop can write. Mirrors `KNOWN_DURABLE_SINKS`
# (core/loop_methods2.py) as on-disk paths.
_DURABLE_SINKS = (
    DEFAULT_EPISODIC_MEMORY_PATH,
    DEFAULT_PROCEDURAL_MEMORY_PATH,
    DEFAULT_MEMORY_CONSOLIDATION_PATH,
    DEFAULT_PERSISTENT_PATH,
    DEFAULT_SOURCE_REGISTRY_PATH,
    DEFAULT_USER_PROFILE_PATH,
    DEFAULT_ASSUMPTIONS_PATH,
    Path("data") / "memory_writes.jsonl",
)


def _durable_snapshot(workspace: Path) -> dict[str, str]:
    """Content hash of every durable sink; absent files are recorded as absent."""
    snap: dict[str, str] = {}
    for rel in _DURABLE_SINKS:
        target = workspace / rel
        if target.exists():
            snap[str(rel)] = hashlib.sha256(target.read_bytes()).hexdigest()
        else:
            snap[str(rel)] = "<absent>"
    return snap


def _drive_one_cycle(
    agent: AgentLoop, question: str, *, task_id: str | None = None
) -> str:
    """Run a full, deterministic no-tool cycle (planner and synthesizer faked)."""
    agent.planner = FakePlanner(sources=[])
    agent.llm = FakeLLM(responses=[SYNTH_GENERAL] * 4)
    return agent.run(user_question=question, file_hint=None, task_id=task_id)


def _replayable_episode(question: str) -> EpisodeRecord:
    """An episode that satisfies every fast-path precondition.

    `usage_eligible=True` and `completion_state="achieved"` are explicit:
    these tests exercise the retrieval and replay MECHANISM, not the
    eligibility policy (2c) or the completion axis (MIR-057), both of which
    fail-close on unclassified episodes and have their own suites.
    """
    return EpisodeRecord(
        goal="answer a general-knowledge question",
        question=question,
        outcome="success",  # type: ignore[arg-type]
        summary="Answered from general knowledge.",
        completion_state="achieved",
        verified_chunks=1,
        unverified_chunks=0,
        replan_exhausted=False,
        answer_quality_score=1.0,
        tools_used=[],
        full_answer="CACHED-ANSWER-FROM-EPISODIC-MEMORY",
        usage_eligible=True,
        id="ep-replay-1",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _episode(question: str) -> EpisodeRecord:
    return EpisodeRecord(
        goal="ship the release notes",
        question=question,
        outcome="success",  # type: ignore[arg-type]
        summary="Checked the changelog with file_read and summarised it.",
        # Wiring suite: seed past the completion gate (MIR-057), which has
        # its own suite, so this stays a test about which stores are held.
        completion_state="achieved",
        verified_chunks=3,
        unverified_chunks=0,
        replan_exhausted=False,
        answer_quality_score=0.9,
        tools_used=["file_read"],
        usage_eligible=True,   # see _replayable_episode: mechanism, not policy
        id="ep-wiring-1",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------
# 1. GUARD — the interactive profile is fully wired (must keep passing).
# --------------------------------------------------------------------------
def test_interactive_profile_has_every_memory_store(tmp_path: Path) -> None:
    agent = _interactive(tmp_path)

    assert agent.memory is not None, "working memory"
    assert agent.persistent_store is not None, "persistent/semantic memory"
    assert agent.episodic_store is not None, "episodic memory"
    assert agent.procedural_store is not None, "procedural memory"
    assert agent.consolidation_store is not None, "consolidation"


# --------------------------------------------------------------------------
# 2. FAIL-BEFORE — the unattended agent must have experience memory.
# --------------------------------------------------------------------------
def test_autonomous_profile_has_experience_stores(tmp_path: Path) -> None:
    agent = _autonomous(tmp_path)

    # Persistent memory already reaches this path today; the experience
    # stores are the gap.
    assert agent.persistent_store is not None, "regression: persistent memory lost"
    assert agent.episodic_store is not None, (
        "the unattended agent records no episodes: episodic memory is created "
        "only under with_memory=True (app/bootstrap.py:151)"
    )
    assert agent.procedural_store is not None, (
        "the unattended agent builds no reusable procedures"
    )


# --------------------------------------------------------------------------
# 3. FAIL-BEFORE — behavioural proof, not just a constructor check.
#    Experience accumulated on disk is invisible to the unattended agent.
# --------------------------------------------------------------------------
def test_autonomous_agent_reads_experience_accumulated_on_disk(tmp_path: Path) -> None:
    question = "summarise the changelog for the release notes"

    # A human-driven session banked a successful episode.
    EpisodicMemoryStore(tmp_path / DEFAULT_EPISODIC_MEMORY_PATH).save(_episode(question))

    # The interactive agent can see it — this is the reference behaviour.
    assert _interactive(tmp_path)._retrieve_experience_memory(question).strip(), (
        "precondition failed: the interactive agent should surface the episode"
    )

    # The unattended agent, asked the very same thing, must see it too.
    recalled = _autonomous(tmp_path)._retrieve_experience_memory(question)
    assert recalled.strip(), (
        "the unattended agent is blind to experience that exists on disk: "
        "the same episode the interactive agent recalls returns nothing here"
    )


# --------------------------------------------------------------------------
# 4. GUARD — `--ask` stays isolated. A fix that widens memory too broadly
#    (e.g. by flipping a default) must fail here.
# --------------------------------------------------------------------------
def test_one_shot_profile_stays_isolated(tmp_path: Path) -> None:
    EpisodicMemoryStore(tmp_path / DEFAULT_EPISODIC_MEMORY_PATH).save(_episode("q"))
    agent = _one_shot(tmp_path)

    # main.py:2011-2014 promises "no memory, fresh session" for one-shot runs;
    # the live-probe and audit workflows depend on that isolation.
    assert agent.memory is None, "one-shot must not carry working memory"
    assert agent.persistent_store is None, "one-shot must not read persistent memory"
    assert agent.episodic_store is None, "one-shot must not read episodic memory"
    assert agent.procedural_store is None, "one-shot must not read procedural memory"


# --------------------------------------------------------------------------
# 5. COMPOSITION — experience memory is a genuinely independent axis.
#    It must not be reachable only through persistent memory.
# --------------------------------------------------------------------------
def test_experience_stores_are_independent_of_persistent(tmp_path: Path) -> None:
    agent = build_agent(
        tmp_path,
        with_memory=False,
        with_persistent=False,
        with_experience=True,
        approval_provider=None,
    )

    assert agent.episodic_store is not None, (
        "experience memory must not depend on persistent memory being enabled"
    )
    assert agent.procedural_store is not None
    assert agent.consolidation_store is not None
    # ...while the other two axes stay off.
    assert agent.persistent_store is None
    assert agent.memory is None


# --------------------------------------------------------------------------
# 6. Axis 2b — reading experience must not imply replaying an answer.
# --------------------------------------------------------------------------
def test_autonomous_profile_reads_but_never_replays(tmp_path: Path) -> None:
    question = "how much is two plus two"
    cached = _replayable_episode(question)
    EpisodicMemoryStore(tmp_path / DEFAULT_EPISODIC_MEMORY_PATH).save(cached)

    # CONTROL: the interactive profile *does* replay this episode verbatim.
    # Without this the test below could pass for the wrong reason (e.g. the
    # episode not being replay-eligible at all).
    interactive_answer = _drive_one_cycle(_interactive(tmp_path), question)
    assert interactive_answer == cached.full_answer, (
        "precondition failed: the episode is not actually fast-path eligible"
    )

    # The unattended profile may read the same episode, but must run a real
    # cycle instead of serving the stored answer.
    autonomous = _autonomous(tmp_path)
    assert autonomous._retrieve_experience_memory(question).strip(), (
        "the unattended agent should still be able to READ this episode"
    )
    answer = _drive_one_cycle(autonomous, question)
    assert answer != cached.full_answer, (
        "the unattended agent replayed a stored answer: episodic_replay=False "
        "must keep the fast path from firing"
    )
    events = [
        json.loads(line)
        for line in (Path(autonomous.log.path)).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert not [e for e in events if e.get("event") == "episodic_fast_path"], (
        "episodic_fast_path fired on a profile that forbids replay"
    )


# --------------------------------------------------------------------------
# 7. SAFETY — holding the stores must not by itself permit writing.
#    Zero delta across every durable sink (MIR-038 / MGA-09 shape).
#
#    Originally this asserted that the *unattended profile* writes nothing.
#    Sub-step 2d deliberately gave that profile a one-sink allowlist, so the
#    assertion is now made against an EMPTY allowlist directly: the property
#    worth pinning is "stores present + nothing permitted => nothing written",
#    which must hold no matter what the shipped profile currently allows.
#    Per-sink behaviour of the real profile is covered by
#    tests/test_autonomous_episode_writeback.py.
# --------------------------------------------------------------------------
def test_stores_without_permission_write_nothing(tmp_path: Path) -> None:
    question = "how much is two plus two"

    # CONTROL: the same cycle on the interactive profile DOES write durable
    # state. Without this, a zero delta below would prove nothing — the cycle
    # might simply never reach a write.
    control_ws = tmp_path / "control"
    control_ws.mkdir()
    before_control = _durable_snapshot(control_ws)
    _drive_one_cycle(_interactive(control_ws), question)
    assert _durable_snapshot(control_ws) != before_control, (
        "precondition failed: this cycle does not write durable state at all"
    )

    # Same stores, no permissions: the cycle must change nothing.
    auto_ws = tmp_path / "unattended"
    auto_ws.mkdir()
    agent = build_agent(
        auto_ws,
        approval_provider=None,
        **{**UNATTENDED_MEMORY_PROFILE, "durable_writes": frozenset()},
    )
    before = _durable_snapshot(auto_ws)
    answer = _drive_one_cycle(agent, question)
    after = _durable_snapshot(auto_ws)

    assert answer, "the cycle must actually produce an answer"
    changed = [k for k in before if before[k] != after[k]]
    assert not changed, (
        f"an empty durable_writes allowlist still wrote durable state: {changed}"
    )
