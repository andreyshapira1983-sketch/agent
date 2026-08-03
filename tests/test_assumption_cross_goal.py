"""MIR-027 — assumptions accumulate across unrelated goals in one session.

The registry suspected the mechanism (`loop.py` restores the store's rows for
`self.log.trace_id`) but called the scope unconfirmed: one-shot mode mints a
fresh trace id per turn, so only a shared-run-id session could show it. The
scope is now pinned (2026-08-03):

    `build_agent` creates ONE TraceLogger per agent, the REPL serves every
    turn with that agent, and the restore block keys on the session-lifetime
    trace id — so every turn inherits every earlier turn's assumptions, and
    `_run_assumptions_current` injects them into the synthesizer prompt of a
    goal they were never extracted from.

The restore's own comment states a narrower intent ("a previous failed
attempt in the same session"), and the `--resume` path legitimately reuses a
trace id for the SAME goal — which is why these are CHARACTERIZATION tests:
they lock today's behaviour so the mechanism stays executable, they do not
endorse it. The fix is a scoping decision (what identity should key the
restore) recorded in MIR-027 for the operator; if the ruling changes the
behaviour, these tests are the spec of what must change.
"""
from __future__ import annotations

from pathlib import Path

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
    events: list[tuple[str, dict | None]] = []
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


def test_an_unrelated_second_goal_inherits_the_first_goals_assumptions(tmp_path):
    """The MIR-027 pair, reproduced deterministically: goal B restores and
    carries assumptions extracted from goal A's question."""
    agent, events = _session_agent(tmp_path)

    agent.run("Сколько строк в файле журнала?")
    first_texts = [a.text for a in agent.last_assumptions.assumptions]
    assert any("Russian-language" in t for t in first_texts), "precondition"

    events.clear()
    agent.run("What is 2 plus 2?")

    restored = [p for e, p in events if e == "assumptions_restored"]
    assert restored and restored[0]["count"] >= 1, (
        "the second turn restored nothing — MIR-027's mechanism has changed; "
        "update the registry entry alongside this test"
    )
    second_texts = [a.text for a in agent.last_assumptions.assumptions]
    assert any("Russian-language" in t for t in second_texts), (
        "goal A's language assumption did not survive into goal B"
    )


def test_the_inherited_assumptions_reach_the_synthesizer_prompt_block(tmp_path):
    """Accumulation is not just storage noise: the restored foreign
    assumptions are rendered into the prompt block the synthesizer receives
    for the unrelated goal — that is the harm surface."""
    agent, _ = _session_agent(tmp_path)
    agent.run("Сколько строк в файле журнала?")
    agent.run("What is 2 plus 2?")
    block = agent.last_assumptions.to_prompt_block()
    assert "Russian-language" in block


def test_a_fresh_trace_id_does_not_inherit(tmp_path):
    """The one-shot counterpart the registry already believed: a new agent
    with its own trace id (one-shot mints one per turn) restores nothing,
    even over the same store file."""
    agent1, _ = _session_agent(tmp_path)
    agent1.run("Сколько строк в файле журнала?")

    agent2, _ = _session_agent(tmp_path, trace_id="trace_one_shot_turn")
    agent2.run("What is 2 plus 2?")
    texts = [a.text for a in agent2.last_assumptions.assumptions]
    assert not any("Russian-language" in t for t in texts)
