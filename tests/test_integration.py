"""Full Control Loop integration test (§3 + §5 + §12.4 + §1).

End-to-end: CLI input -> Planner -> Plan -> Policy -> file_read -> ToolResult
-> Verify -> Synthesize. Asserted on both the returned answer AND the
structured JSONL trace log.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tests.conftest import FakeLLM


PLANNER_FILE_READ_RESPONSE = json.dumps(
    {
        "reasoning": "Question references the hinted file.",
        "steps": [
            {
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "rationale": "read the hinted file",
            }
        ],
    }
)

SYNTHESIZED_ANSWER = (
    "Conclusion: The file lists three numbered items. [file:doc.txt]\n"
    "Facts:\n"
    "- Item alpha is mentioned. [file:doc.txt]\n"
    "- Item beta is mentioned. [file:doc.txt]\n"
    "- Item gamma is mentioned. [file:doc.txt]\n"
    "Sources:\n"
    "1. file:doc.txt - doc.txt\n"
    "Confidence: high\n"
    "Unverified: nothing\n"
)


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent(workspace: Path, llm: FakeLLM) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))

    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
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
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def test_full_cycle_with_file(workspace: Path) -> None:
    (workspace / "doc.txt").write_text(
        "1. alpha\n2. beta\n3. gamma\n", encoding="utf-8"
    )

    llm = FakeLLM(responses=[PLANNER_FILE_READ_RESPONSE, SYNTHESIZED_ANSWER])
    agent, log_path = _build_agent(workspace, llm)

    answer = agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")

    # --- 1) Returned answer obeys the Output Contract and cites the file
    # MVP-14.4: matched citations get rewritten to [verified:...] form.
    assert "Conclusion:" in answer
    assert "[verified:file:doc.txt]" in answer
    assert "Confidence:" in answer

    # --- 2) Both LLM calls happened in the expected order
    assert len(llm.calls) == 2, "exactly one planner call + one synthesis call"
    planner_call, synth_call = llm.calls
    assert "PLANNER_MODE" in planner_call["system"] or "planner of an autonomous" in planner_call["system"]
    assert planner_call["temperature"] == 0.0
    assert "Every factual sentence in Conclusion MUST end with a source label" in synth_call["system"]
    assert '<evidence source="file:doc.txt">' in synth_call["user"]
    assert "<allowed_citations>" in synth_call["user"]
    assert "[file:doc.txt]" in synth_call["user"]
    assert "alpha" in synth_call["user"] and "beta" in synth_call["user"]

    # --- 3) JSONL trace contains the full Control Loop sequence
    events = _events(log_path)
    event_names = [e["event"] for e in events]
    for expected in (
        "observe",
        "interpret",
        "planner",
        "plan",
        "act",
        "policy",
        "tool_call",
        "tool_result",
        "verify",
        "respond",
    ):
        assert expected in event_names, f"missing event: {expected} (got {event_names})"

    by_event = {e["event"]: e for e in events}

    # planner: chose file_read
    assert by_event["planner"]["payload"]["tools_chosen"] == ["file_read"]
    assert by_event["planner"]["payload"]["warnings"] == []

    # policy: allowed (read-only tool)
    assert by_event["policy"]["payload"]["decision"] == "allow"
    assert by_event["policy"]["payload"]["subject"] == "file_read"

    # tool_result: success
    assert by_event["tool_result"]["payload"]["status"] == "success"

    # verify: ok, no issues
    assert by_event["verify"]["payload"]["ok"] is True
    assert by_event["verify"]["payload"]["issues"] == []
    assert by_event["verify"]["payload"]["source_label"] == "file:doc.txt"

    # respond: cited the file
    assert by_event["respond"]["payload"]["sources"] == ["file:doc.txt"]


def test_full_cycle_no_tools_general_knowledge(workspace: Path) -> None:
    """When the planner returns an empty plan, synthesis still runs and the
    answer is labelled [general-knowledge]."""
    empty_plan = json.dumps({"reasoning": "no tools needed", "steps": []})
    gk_answer = (
        "Conclusion: 2+2 equals 4. [general-knowledge]\n"
        "Facts:\n- Arithmetic gives 4. [general-knowledge]\n"
        "Sources:\n1. general-knowledge - general-knowledge\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    llm = FakeLLM(responses=[empty_plan, gk_answer])
    agent, log_path = _build_agent(workspace, llm)

    answer = agent.run(user_question="What is 2+2?", file_hint=None)

    # MVP-14.4.x: `[general-knowledge]` is rewritten to
    # `[declared:general-knowledge]` (the new self_declared verdict).
    assert "[declared:general-knowledge]" in answer

    events = _events(log_path)
    by_event = {e["event"]: e for e in events}

    assert by_event["planner"]["payload"]["tools_chosen"] == []
    assert by_event["plan"]["extra"]["steps"] == 0
    # No tool execution events should appear for an empty plan
    assert "tool_call" not in {e["event"] for e in events}
    assert by_event["respond"]["payload"]["sources"] == ["general-knowledge"]
