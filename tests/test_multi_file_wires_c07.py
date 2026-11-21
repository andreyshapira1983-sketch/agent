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
