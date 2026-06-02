"""Integration tests for Working Memory inside the Control Loop.

Three behaviours we promise in MVP-4:
  - `memory_inject` fires when the agent runs with prior turns in memory
    AND the planner actually receives the conversation_history block.
  - `memory_cache_hit` fires when the planner picks a tool whose
    (name, arguments) is already in the artifact cache — and the Executor
    skips Policy + ToolCall + Verify for that step.
  - `memory_clear` resets both turns and artifacts cleanly.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tests.conftest import FakeLLM


PLAN_FILE_READ = json.dumps(
    {
        "reasoning": "Read the hinted file.",
        "steps": [
            {"tool": "file_read", "arguments": {"path": "doc.txt"}, "rationale": "..."}
        ],
    }
)
PLAN_EMPTY = json.dumps({"reasoning": "follow-up answerable from history", "steps": []})

SYNTH_FILE = (
    "Conclusion: The file lists items. [file:doc.txt]\n"
    "Facts:\n- alpha [file:doc.txt]\n"
    "Sources:\n1. file:doc.txt - doc.txt\n"
    "Confidence: high\nUnverified: nothing\n"
)
SYNTH_FOLLOWUP = (
    "Conclusion: Yes, the file contains alpha. [file:doc.txt]\n"
    "Facts:\n- alpha is in the file [file:doc.txt]\n"
    "Sources:\n1. file:doc.txt - doc.txt\n"
    "Confidence: medium\nUnverified: nothing\n"
)


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent_with_memory(workspace: Path, llm: FakeLLM):
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
    memory = WorkingMemory()
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id,
        log_dir=workspace / "logs",
        verbose=False,
    )
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=memory,
    )
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    return agent, memory, log_path


# ---------- memory_inject ----------

def test_memory_inject_fires_only_after_first_turn(workspace: Path) -> None:
    (workspace / "doc.txt").write_text("alpha beta gamma\n", encoding="utf-8")

    llm = FakeLLM(
        responses=[
            PLAN_FILE_READ, SYNTH_FILE,        # turn 1
            PLAN_EMPTY, SYNTH_FOLLOWUP,        # turn 2
        ]
    )
    agent, memory, log_path = _build_agent_with_memory(workspace, llm)

    # Turn 1 — no prior turns, planner must not see <conversation_history>
    agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")
    turn1_planner_user = [c for c in llm.calls if "PLANNER_MODE" in c["system"]][0]["user"]
    assert "<conversation_history>" not in turn1_planner_user

    # Turn 2 — memory now has 1 turn, planner MUST see it
    agent.run(user_question="Does it mention alpha?", file_hint="doc.txt")

    events = _events(log_path)
    inject_events = [e for e in events if e["event"] == "memory_inject"]

    # Exactly one memory_inject — for turn 2
    assert len(inject_events) == 1
    payload = inject_events[0]["payload"]
    assert payload["session_id"] == memory.session_id
    assert payload["turns_visible"] == 1
    assert payload["history_chars"] > 0
    assert payload["artifacts_cached"] == 1

    # And the planner call for turn 2 actually carried the history block
    planner_calls = [c for c in llm.calls if "PLANNER_MODE" in c["system"]]
    assert "<conversation_history>" in planner_calls[1]["user"]
    assert "What is in doc.txt?" in planner_calls[1]["user"]


# ---------- memory_cache_hit ----------

def test_cache_hit_skips_policy_and_tool_on_repeat(workspace: Path) -> None:
    (workspace / "doc.txt").write_text("alpha beta gamma\n", encoding="utf-8")

    # Both turns ask the planner to call file_read with the SAME args.
    llm = FakeLLM(
        responses=[
            PLAN_FILE_READ, SYNTH_FILE,        # turn 1: tool actually runs
            PLAN_FILE_READ, SYNTH_FOLLOWUP,    # turn 2: cache MUST short-circuit
        ]
    )
    agent, _memory, log_path = _build_agent_with_memory(workspace, llm)

    agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")
    agent.run(user_question="Read it again, please.", file_hint="doc.txt")

    events = _events(log_path)

    # 2x planner phases must have run
    assert len([e for e in events if e["event"] == "planner"]) == 2

    # Tool actually executed only ONCE — for turn 1.
    # Turn 2 must have produced a cache_hit instead.
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    tool_results = [e for e in events if e["event"] == "tool_result"]
    cache_hits = [e for e in events if e["event"] == "memory_cache_hit"]

    assert len(tool_calls) == 1, f"tool_call should fire once, got {len(tool_calls)}"
    assert len(tool_results) == 1
    assert len(cache_hits) == 1

    hit_payload = cache_hits[0]["payload"]
    assert hit_payload["tool"] == "file_read"
    assert hit_payload["arguments"] == {"path": "doc.txt"}
    assert hit_payload["label"] == "file:doc.txt"
    assert hit_payload["stored_at_turn"] == 1

    # The act/policy/verify trio between turn 1 and turn 2 also tells a story:
    # turn 1 fires (act, policy, tool_call, tool_result, verify),
    # turn 2 should NOT have an act/policy pair for the cached step.
    acts = [e for e in events if e["event"] == "act"]
    policies = [e for e in events if e["event"] == "policy"]
    assert len(acts) == 1
    assert len(policies) == 1


# ---------- memory_clear ----------

def test_memory_clear_resets_state(workspace: Path) -> None:
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")

    llm = FakeLLM(responses=[PLAN_FILE_READ, SYNTH_FILE])
    agent, memory, log_path = _build_agent_with_memory(workspace, llm)

    agent.run(user_question="Read doc.txt", file_hint="doc.txt")
    assert len(memory.turns) == 1
    assert memory.cache_lookup("file_read", {"path": "doc.txt"}) is not None

    # Simulate the REPL `:clear` command: agent.memory.clear() + a log event
    agent.memory.clear()
    agent.log.log("memory_clear", {"session_id": memory.session_id})

    assert memory.turns == []
    assert memory.cache_lookup("file_read", {"path": "doc.txt"}) is None
    assert memory.summary()["turns"] == 0
    assert memory.summary()["artifacts_cached"] == 0

    # The clear event must be visible in the trace
    events = _events(log_path)
    clear_events = [e for e in events if e["event"] == "memory_clear"]
    assert len(clear_events) == 1
    assert clear_events[0]["payload"]["session_id"] == memory.session_id


def test_after_clear_next_turn_has_no_memory_inject(workspace: Path) -> None:
    """After :clear, the very next turn must look like a fresh session:
    no `memory_inject` event, planner prompt has no <conversation_history>.
    """
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")

    llm = FakeLLM(
        responses=[
            PLAN_FILE_READ, SYNTH_FILE,    # turn 1
            PLAN_EMPTY, SYNTH_FOLLOWUP,    # turn 2 (after clear)
        ]
    )
    agent, memory, log_path = _build_agent_with_memory(workspace, llm)

    agent.run(user_question="Read doc.txt", file_hint="doc.txt")
    assert len(memory.turns) == 1

    # Simulate the `:clear` command
    agent.memory.clear()
    agent.log.log("memory_clear", {"session_id": memory.session_id})
    assert memory.turns == []
    assert memory.cache_lookup("file_read", {"path": "doc.txt"}) is None

    # Next turn — should behave like turn 1 of a brand new session
    agent.run(user_question="What is 2+2?", file_hint=None)

    events = _events(log_path)
    inject_events = [e for e in events if e["event"] == "memory_inject"]

    # The only valid memory_inject would have come BEFORE clear (and turn 1
    # had no prior turns, so there were none). Post-clear must produce zero.
    assert inject_events == [], (
        f"expected zero memory_inject after clear, got {len(inject_events)}"
    )

    # And the planner call for the post-clear turn must NOT carry history
    planner_calls = [c for c in llm.calls if "PLANNER_MODE" in c["system"]]
    # planner_calls[0] = turn 1, planner_calls[1] = post-clear turn
    assert "<conversation_history>" not in planner_calls[1]["user"]


def test_after_clear_repeat_tool_call_runs_again(workspace: Path) -> None:
    """After :clear, the artifact cache must also be empty: a repeat of the
    exact same file_read MUST go through Policy + Tool again, not short-
    circuit via memory_cache_hit.
    """
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")

    llm = FakeLLM(
        responses=[
            PLAN_FILE_READ, SYNTH_FILE,        # turn 1: real tool_call
            PLAN_FILE_READ, SYNTH_FOLLOWUP,    # turn 2 (after clear): must be real again
        ]
    )
    agent, _memory, log_path = _build_agent_with_memory(workspace, llm)

    agent.run(user_question="Read doc.txt", file_hint="doc.txt")
    agent.memory.clear()
    agent.log.log("memory_clear", {"session_id": agent.memory.session_id})
    agent.run(user_question="Read doc.txt again", file_hint="doc.txt")

    events = _events(log_path)

    # TWO real tool_call events — one per agent.run, with the cache empty in between.
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert len(tool_calls) == 2, (
        f"expected 2 tool_call events (cache cleared between turns), got {len(tool_calls)}"
    )

    # And ZERO memory_cache_hit events — the second call must not see the cache.
    cache_hits = [e for e in events if e["event"] == "memory_cache_hit"]
    assert cache_hits == [], (
        f"expected no cache_hit after clear, got {[e['payload'] for e in cache_hits]}"
    )

    # Policy was checked both times (defense layer still intact)
    policies = [e for e in events if e["event"] == "policy"]
    assert len(policies) == 2
    assert all(p["payload"]["decision"] == "allow" for p in policies)


def test_no_memory_means_no_memory_events(workspace: Path) -> None:
    """Sanity: with memory=None, the loop must never emit memory_* events."""
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")

    llm = FakeLLM(responses=[PLAN_FILE_READ, SYNTH_FILE])

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=None,
    )
    log_path = workspace / "logs" / f"{trace_id}.jsonl"

    agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")

    events = _events(log_path)
    memory_events = [e for e in events if e["event"].startswith("memory_")]
    assert memory_events == []
