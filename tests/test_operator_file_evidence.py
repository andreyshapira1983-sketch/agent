from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _agent(
    workspace: Path,
    *,
    llm_responses: list[str],
    sources: list[dict],
    memory: WorkingMemory | None = None,
    verifier_enabled: bool = True,
) -> tuple[AgentLoop, FakePlanner, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    llm = FakeLLM(responses=llm_responses)
    planner = FakePlanner(sources=sources)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=planner,
        memory=memory,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=verifier_enabled,
    )
    return agent, planner, workspace / "logs" / f"{trace_id}.jsonl"


def test_explicit_read_task_file_forces_hinted_file_evidence(workspace: Path):
    (workspace / "task.md").write_text("Operator Task Layer requirements.", encoding="utf-8")
    agent, planner, _ = _agent(
        workspace,
        llm_responses=[
            "Conclusion: task file describes Operator Task Layer [file:task.md]."
        ],
        sources=[],
    )

    answer = agent.run(
        "Прочитай файл задания. Ничего не меняй. Составь план.",
        file_hint="task.md",
    )

    assert planner.calls
    assert "[verified:file:task.md]" in answer
    assert any(ev.source_id == "file:task.md" for ev in agent.last_provenance.evidences)


def test_explicit_read_uses_cached_file_artifact_as_evidence(workspace: Path):
    (workspace / "task.md").write_text("Cached task evidence.", encoding="utf-8")
    memory = WorkingMemory()
    agent, planner, log_path = _agent(
        workspace,
        llm_responses=[
            "Conclusion: first read [file:task.md].",
            "Conclusion: cached read [file:task.md].",
        ],
        sources=[{
            "tool": "file_read",
            "arguments": {"path": "task.md"},
            "label": "file:task.md",
            "expected_outcome": "read task.md",
        }],
        memory=memory,
    )

    first = agent.run("Read task.md", file_hint="task.md")
    planner.sources = []
    second = agent.run("Прочитай файл задания ещё раз.", file_hint="task.md")

    events = _events(log_path)
    assert "[verified:file:task.md]" in first
    assert "[verified:file:task.md]" in second
    assert any(event["event"] == "memory_cache_hit" for event in events)


def test_file_scope_notice_separates_requested_path_from_actual_hint(workspace: Path):
    (workspace / "operator_task_layer_request.md").write_text(
        "Operator Task Layer request.",
        encoding="utf-8",
    )
    agent, _, _ = _agent(
        workspace,
        llm_responses=[
            "Conclusion: I read the available task file [file:.\\operator_task_layer_request.md]."
        ],
        sources=[],
        verifier_enabled=False,
    )

    answer = agent.run(
        "Прочитай файл задания .\\docs\\operator_task_layer_request.md.",
        file_hint=".\\operator_task_layer_request.md",
    )

    assert "Evidence scope: I only have evidence for .\\operator_task_layer_request.md." in answer
    assert "I did not verify .\\docs\\operator_task_layer_request.md." in answer
