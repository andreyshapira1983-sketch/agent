"""MIR-027 — a new goal starts with a clean set of active assumptions.

The operator's ruling (2026-08-03, recorded in MIR-027): «Сохранить — не
значит постоянно помнить». History is an ARCHIVE — it may be stored, but it
must not be auto-activated into an unrelated new task; the old rises only
through an explicit, applicability-checked retrieval (the memory-lifecycle
contract's territory), never by default.

Before the fix these were characterization tests pinning the leak: the
restore block keyed on the session-lifetime trace id, so in the REPL every
turn inherited every earlier turn's assumptions and `_run_assumptions_current`
injected them into the synthesizer prompt of a goal they were never extracted
from — measured live with «The user expects a Russian-language response
(confidence=90%)» steering an unrelated English arithmetic question. The
cross-turn auto-restore also served nothing else: one-shot mints a fresh
trace id per turn, `--resume` builds a fresh agent (fresh trace id) and
carries the QUESTION instead, and failed replan attempts share the in-memory
registry within one `run()` call.

Now they are the ruling's spec: no cross-turn inheritance, the archive rows
stay written, and the fresh-trace-id control keeps holding.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.assumption_registry import AssumptionStore
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tools.base import ToolRegistry

_ANSWER = (
    "Conclusion: done.\nSources:\n1. [general-knowledge]\n"
    "Confidence: high\nUnverified: nothing"
)


class _FixedLLM:
    provider = "mock"
    model = "mock-1"

    def complete(self, system, user, **kw):
        return _ANSWER

    def stream(self, system, user, **kw):
        yield _ANSWER


def _session_agent(tmp_path: Path, trace_id: str = "trace_repl_session"):
    """One agent = one REPL session: a single TraceLogger for its lifetime,
    exactly how `app.bootstrap.build_agent` wires it. A different `trace_id`
    models a separate one-shot turn over the same store."""
    llm = _FixedLLM()
    registry = ToolRegistry()
    logger = TraceLogger(trace_id=trace_id, log_dir=tmp_path, verbose=False)
    # Payloads are whatever the loop logs (dicts, Pydantic models, None) —
    # the spy records, it does not constrain.
    events: list[tuple[str, Any]] = []
    original_log = logger.log

    def spy(event, payload=None, **kw):
        events.append((event, payload))
        return original_log(event, payload, **kw)

    logger.log = spy  # type: ignore[method-assign]
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        assumption_store=AssumptionStore(tmp_path / "assumptions.jsonl"),
    )
    return agent, events


def test_an_unrelated_second_goal_starts_clean(tmp_path):
    """The ruling's core: goal B neither restores nor carries goal A's
    assumptions — no `assumptions_restored` event, no foreign text."""
    agent, events = _session_agent(tmp_path)

    agent.run("Сколько строк в файле журнала?")
    first_texts = [a.text for a in agent.last_assumptions.assumptions]
    assert any("Russian-language" in t for t in first_texts), "precondition"

    events.clear()
    agent.run("What is 2 plus 2?")

    restored = [p for e, p in events if e == "assumptions_restored"]
    assert not restored, (
        "a new goal auto-restored archived assumptions — the MIR-027 leak "
        "is back; the archive must stay dormant (operator ruling 2026-08-03)"
    )
    second_texts = [a.text for a in agent.last_assumptions.assumptions]
    assert not any("Russian-language" in t for t in second_texts), (
        "goal A's language assumption leaked into unrelated goal B"
    )


def test_the_second_goals_prompt_block_carries_only_its_own_assumptions(tmp_path):
    """The harm surface, inverted: the synthesizer block for goal B must not
    carry goal A's assumptions."""
    agent, _ = _session_agent(tmp_path)
    agent.run("Сколько строк в файле журнала?")
    agent.run("What is 2 plus 2?")
    block = agent.last_assumptions.to_prompt_block()
    assert "Russian-language" not in block


def test_the_archive_keeps_the_first_goals_rows(tmp_path):
    """«Сохранить — не значит постоянно помнить»: dormant is not deleted.
    Goal A's assumptions stay written in the store after goal B ran."""
    agent, _ = _session_agent(tmp_path)
    agent.run("Сколько строк в файле журнала?")
    agent.run("What is 2 plus 2?")
    store = AssumptionStore(tmp_path / "assumptions.jsonl")
    archived = store.load_by_run("trace_repl_session")
    assert any("Russian-language" in a.text for a in archived), (
        "the fix must silence auto-activation, not destroy the archive"
    )


def test_a_fresh_trace_id_does_not_inherit(tmp_path):
    """The one-shot counterpart, unchanged by the fix: a new agent with its
    own trace id restores nothing over the same store file."""
    agent1, _ = _session_agent(tmp_path)
    agent1.run("Сколько строк в файле журнала?")

    agent2, _ = _session_agent(tmp_path, trace_id="trace_one_shot_turn")
    agent2.run("What is 2 plus 2?")
    texts = [a.text for a in agent2.last_assumptions.assumptions]
    assert not any("Russian-language" in t for t in texts)


# ── the corners beyond the REPL (subsystem map, 2026-08-03) ──────────────────
#
# The daemon tick builds ONE agent per tick (`agent_tick.py`) and serves every
# queued task with it — the same one-agent-many-goals shape as the REPL. Its
# memory profile (`UNATTENDED_MEMORY_PROFILE`) allowlists only
# {"episode", "hygiene"} durable sinks, so the assumptions sink is denied.
# These tests pin that shape directly: they are corner pins, not fix proofs —
# the daemon was already leak-free pre-fix (fresh trace id per tick + saves
# suppressed), and they keep it that way if either premise ever changes.


def _daemon_shaped_agent(tmp_path: Path):
    """An AgentLoop with the unattended profile's durable-writes allowlist,
    a live assumption store handle, and one trace id — the daemon-tick shape."""
    llm = _FixedLLM()
    registry = ToolRegistry()
    logger = TraceLogger(trace_id="trace_daemon_tick", log_dir=tmp_path, verbose=False)
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        assumption_store=AssumptionStore(tmp_path / "assumptions.jsonl"),
        durable_writes=frozenset({"episode", "hygiene"}),
    )


def test_the_unattended_profile_never_saves_assumptions(tmp_path):
    """The daemon's allowlist denies the assumptions sink: after two goal
    tasks through one agent, the archive file holds nothing."""
    agent = _daemon_shaped_agent(tmp_path)
    agent.run("Сколько строк в файле журнала?")
    agent.run("What is 2 plus 2?")
    store = AssumptionStore(tmp_path / "assumptions.jsonl")
    assert store.load_recent(50) == []


def test_two_daemon_tasks_through_one_agent_stay_isolated(tmp_path):
    """Cross-TASK isolation on the daemon shape: the second queued goal's
    active set carries nothing from the first, in memory or in prompt."""
    agent = _daemon_shaped_agent(tmp_path)
    agent.run("Сколько строк в файле журнала?")
    agent.run("What is 2 plus 2?")
    texts = [a.text for a in agent.last_assumptions.assumptions]
    assert not any("Russian-language" in t for t in texts)
    assert "Russian-language" not in agent.last_assumptions.to_prompt_block()


def test_the_current_goals_assumptions_do_reach_the_llm_prompt(tmp_path):
    """The positive corner («будет ли это работать вообще»): the CURRENT
    goal's own assumptions are injected into the synthesizer prompt — the
    fix silenced the archive, not the mechanism."""
    llm = _FixedLLM()
    seen_prompts: list[str] = []
    original_complete = llm.complete

    def recording_complete(system, user, **kw):
        seen_prompts.append(user)
        return original_complete(system, user, **kw)

    llm.complete = recording_complete  # type: ignore[method-assign]
    registry = ToolRegistry()
    logger = TraceLogger(trace_id="trace_prompt_check", log_dir=tmp_path, verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        assumption_store=AssumptionStore(tmp_path / "assumptions.jsonl"),
    )
    agent.run("Сколько строк в файле журнала?")
    assert any(
        "<assumptions>" in p and "Russian-language" in p for p in seen_prompts
    ), "the current run's own assumptions never reached an LLM prompt"
