"""C07 — the multi-file verdict's two wires into the loop, bitten through run().

The producer (`prepare_multi_file_review`) has twelve unit tests; neither of
its LOOP consumers had a behavioural observer. Measured 2026-08-08:

- M45 (refusal gate silenced at the loop call site): the only red was
  `test_each_gate_is_called_exactly_once` — an inspect.getsource guard that
  counts call sites, Structural by the map's scale. A refused review could
  fall through to a full cycle and nothing behavioural would notice.
- M46 (forced_sources forced to None): 49 tests across the producer,
  integration, attempt-split and gates suites stayed green — the kernel's
  forced plan was silently replaced by whatever the LLM planner said.

Both wires matter for the same reason: this verdict is KERNEL authority over
what gets read. The refusal stops scope creep past the operator's named file;
the forced plan pins exactly which validated paths are read, bypassing the
planner entirely on attempt 1.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _agent(tmp_path: Path, llm: FakeLLM) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs", verbose=False),
        verifier_enabled=False,
        clarification_enabled=False,
        odd_enabled=False,
    )
    return agent, tmp_path / "logs" / f"{trace_id}.jsonl"


def test_a_refused_review_ends_the_turn_before_any_planning(tmp_path: Path) -> None:
    """Refusal wire: hinted mode + extra named files -> the kernel's message,
    zero LLM calls. Under M45 this red: the question fell through to a cycle."""
    (tmp_path / "hint.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "other.md").write_text("b\n", encoding="utf-8")
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}', "answer"])
    agent, log_path = _agent(tmp_path, llm)

    answer = agent.run("сравни hint.md и other.md", file_hint="hint.md")

    assert llm.calls == [], (
        "a refused multi-file review must end the turn — the planner ran"
    )
    refused = [e for e in _events(log_path) if e["event"] == "multi_file_review_refused"]
    assert refused, "the refusal must be journaled"
    assert answer.strip(), "the operator receives the kernel's message, not silence"


def test_a_forced_review_reads_the_kernels_plan_not_the_llms(tmp_path: Path) -> None:
    """Forced wire: the kernel's validated read-plan executes verbatim.

    The LLM planner's answer names NO tools; if the forced plan is delivered,
    both files are read anyway and the planner is never even consulted on
    attempt 1. Under M46 this red: no file_read ran at all.
    """
    (tmp_path / "a.md").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta\n", encoding="utf-8")
    llm = FakeLLM(
        responses=[
            '{"reasoning":"no tools","sources":[]}',   # would-be planner call
            ("Conclusion: compared. [file:a.md]\nFacts:\n- x [file:a.md]\n"
            "Sources:\n1. file:a.md\nConfidence: high\n"),
        ]
    )
    agent, log_path = _agent(tmp_path, llm)

    agent.run("multi-file review: сравни a.md и b.md")

    acts = [
        e for e in _events(log_path)
        if e["event"] == "tool_call" and e["payload"].get("tool_name") == "file_read"
    ]
    read_paths = sorted(e["payload"]["arguments"]["path"] for e in acts)
    assert read_paths == ["a.md", "b.md"], (
        f"the kernel's forced plan must read exactly its validated paths, "
        f"got {read_paths} — the LLM plan was used instead (M46 hole)"
    )
