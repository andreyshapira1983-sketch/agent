"""The loop must actually collect defect signals, not merely be able to store them.

`tests/test_episode_defect_signals.py` pins the record. This file pins the
*wiring*, because the repo's recurring anti-pattern (`docs/self-audit-lessons.md`
#6) is a mechanism that exists, is unit-tested, and is never reached in
production — which is precisely what the sensors themselves were doing: each
logged its verdict and dropped it.

A run through the real `AgentLoop` (fake LLM, no provider, no network) must bank
an episode whose `defect_signals` is a tuple — `None` here would mean the loop
never collected, and the field would be decoration.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.smart_memory import EpisodicMemoryStore
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

PLAN_EMPTY = json.dumps({"reasoning": "answerable without tools", "steps": []})
SYNTH_OK = (
    "Conclusion: a plain answer [memory:demo].\n"
    "Facts:\n- a fact [memory:demo]\n"
    "Sources:\n1. memory:demo - long_term_memory\n"
    "Confidence: medium\nUnverified: nothing\n"
)


def _run_once(workspace: Path) -> EpisodicMemoryStore:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    logger = TraceLogger(
        trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
    )
    llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK])
    episodic = EpisodicMemoryStore(workspace / "data" / "episodes.jsonl")
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "mem.jsonl"),
        episodic_store=episodic,
    )
    agent.run(user_question="What is the meaning of life?")
    return episodic


def test_a_real_run_banks_a_collected_signal_set(workspace: Path):
    episodes = _run_once(workspace).load()
    assert episodes, "the run banked no episode at all"
    assert episodes[-1].defect_signals is not None, (
        "the loop never collected defect signals — the field is stored but "
        "unreachable in production, which is the anti-pattern it was added to "
        "end"
    )
    assert isinstance(episodes[-1].defect_signals, tuple)


def test_a_clean_run_records_an_empty_set_not_a_missing_one(workspace: Path):
    """A run with nothing wrong says so, rather than staying silent."""
    episodes = _run_once(workspace).load()
    assert episodes[-1].defect_signals == ()


def test_the_loop_resets_signals_per_cycle(workspace: Path):
    """A previous run's faults must not be banked against the next episode."""
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    logger = TraceLogger(
        trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
    )
    llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK, PLAN_EMPTY, SYNTH_OK])
    episodic = EpisodicMemoryStore(workspace / "data" / "episodes.jsonl")
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "mem.jsonl"),
        episodic_store=episodic,
    )
    agent.run(user_question="First question?")
    # Plant a fault as if a sensor had fired, then run again.
    agent._defect_signals.append("planted_fault")
    agent.run(user_question="Second question?")

    last = episodic.load()[-1]
    assert "planted_fault" not in (last.defect_signals or ()), (
        "the second episode inherited the first run's fault; the per-cycle "
        "reset is what keeps a fault attached to the run that caused it"
    )


def test_the_write_event_reports_the_signals(workspace: Path):
    """An operator reading the journal sees the faults beside the verdict."""
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK])
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "mem.jsonl"),
        episodic_store=EpisodicMemoryStore(workspace / "data" / "episodes.jsonl"),
    )
    agent.run(user_question="What is the meaning of life?")

    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    writes = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and '"episodic_memory_write"' in line
    ]
    assert writes, "no episodic_memory_write event was emitted"
    assert "defect_signals" in writes[-1].get("payload", writes[-1])
