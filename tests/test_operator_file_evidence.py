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

    agent.run(
        "Прочитай файл задания. Ничего не меняй. Составь план.",
        file_hint="task.md",
    )

    assert planner.calls
    assert "[verified:file:task.md]" in agent.last_verification.annotated_answer
    assert any(ev.source_id == "file:task.md" for ev in agent.last_provenance.evidences)


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


def test_regular_file_hint_mode_refuses_multi_file_review(workspace: Path):
    (workspace / "a.md").write_text("A", encoding="utf-8")
    (workspace / "main.py").write_text("print('main')", encoding="utf-8")
    (workspace / "core").mkdir()
    (workspace / "core" / "operator_intent.py").write_text("ROUTES = {}", encoding="utf-8")
    agent, planner, log_path = _agent(
        workspace,
        llm_responses=[],
        sources=[],
        memory=WorkingMemory(),
    )

    answer = agent.run(
        r"Проверь .\a.md, .\main.py и .\core\operator_intent.py.",
        file_hint=r".\a.md",
    )

    events = _events(log_path)
    assert "Regular --file mode only permits the hinted file." in answer
    assert r"Available evidence: .\a.md." in answer
    assert r".\main.py" in answer
    assert r".\core\operator_intent.py" in answer
    assert "were not reviewed" in answer
    assert planner.calls == []
    assert agent.llm.calls == []
    assert not [event for event in events if event["event"] == "memory_cache_hit"]
    assert not [event for event in events if event["event"] == "tool_call"]


def test_multi_file_review_rejects_path_traversal_without_llm(workspace: Path):
    agent, planner, _ = _agent(
        workspace,
        llm_responses=[],
        sources=[],
    )

    answer = agent.run(
        r"Use explicit multi-file review mode: read ..\secret.txt and ..\other.md.",
        file_hint=None,
    )

    assert "Multi-file review could not start" in answer
    assert "path traversal is not allowed" in answer
    assert planner.calls == []
    assert agent.llm.calls == []


def test_multi_file_review_rejects_absolute_outside_workspace(workspace: Path):
    (workspace / "a.md").write_text("Alpha file.", encoding="utf-8")
    outside = workspace.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    agent, _, _ = _agent(
        workspace,
        llm_responses=["Conclusion: only Alpha was reviewed [file:a.md]."],
        sources=[],
    )

    answer = agent.run(
        f"Use explicit multi-file review mode: read .\\a.md and {outside}.",
        file_hint=None,
    )

    assert "[verified:file:a.md]" in agent.last_verification.annotated_answer
    assert f"I did not verify {outside}." in answer
    assert {ev.source_id for ev in agent.last_provenance.evidences} == {"file:a.md"}


def test_multi_file_review_reports_posix_absolute_outside_path(workspace: Path):
    (workspace / "a.md").write_text("Alpha file.", encoding="utf-8")
    outside = "/tmp/outside.md"
    agent, _, _ = _agent(
        workspace,
        llm_responses=["Conclusion: only Alpha was reviewed [file:a.md]."],
        sources=[],
    )

    answer = agent.run(
        f"Use explicit multi-file review mode: read .\\a.md and {outside}.",
        file_hint=None,
    )

    assert "[verified:file:a.md]" in agent.last_verification.annotated_answer
    assert f"I did not verify {outside}." in answer
    assert {ev.source_id for ev in agent.last_provenance.evidences} == {"file:a.md"}


def test_multi_file_review_dedupes_duplicate_paths(workspace: Path):
    (workspace / "a.md").write_text("Alpha file.", encoding="utf-8")
    agent, _, log_path = _agent(
        workspace,
        llm_responses=["Conclusion: Alpha was reviewed [file:a.md]."],
        sources=[],
    )

    agent.run(
        r"Use explicit multi-file review mode: read .\a.md and a.md.",
        file_hint=None,
    )

    events = _events(log_path)
    file_reads = [
        event for event in events
        if event["event"] == "tool_call"
        and event["payload"]["tool_name"] == "file_read"
    ]
    assert len(file_reads) == 1
    assert "[verified:file:a.md]" in agent.last_verification.annotated_answer


def test_multi_file_review_reports_missing_files_without_replan_waste(workspace: Path):
    agent, planner, log_path = _agent(
        workspace,
        llm_responses=[],
        sources=[],
    )

    answer = agent.run(
        r"Use explicit multi-file review mode: read .\missing.md and .\absent.md.",
        file_hint=None,
    )

    events = _events(log_path)
    assert "Multi-file review could not start" in answer
    assert "missing file" in answer
    assert planner.calls == []
    assert agent.llm.calls == []
    assert not [event for event in events if event["event"] == "replan"]
