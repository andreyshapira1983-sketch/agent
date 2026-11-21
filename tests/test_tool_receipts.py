"""Tool receipts slice 1a — ledger, redaction, and Tool.invoke hook."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.approval import AutoApprover
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.models import ToolCall
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry_store import SourceRegistryStore
from core.tool_receipts import (
    ToolReceipt,
    ToolReceiptLedger,
    bind_receipt_context,
    default_receipts_path,
    reset_receipt_context,
)
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry
from tools.file_write import FileWriteTool


class _FakeRunTestsTool(Tool):
    name = "run_tests"
    description = "fake tests"
    risk = "reversible"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "timed_out": False,
            "passed": 3,
            "failed": 0,
            "errors": 0,
            "failed_tests": [],
        }


def _runtime_agent(workspace: Path) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(_FakeRunTestsTool())
    llm = FakeLLM(responses=[])
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=PersistentMemoryStore(workspace / "data" / "memory.jsonl"),
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )


def test_append_receipt_persists_and_loads(workspace: Path) -> None:
    path = default_receipts_path(workspace)
    ledger = ToolReceiptLedger(path)
    saved = ledger.append_receipt(
        ToolReceipt(
            receipt_id="trc_test0001",
            operation="file_read",
            status="success",
            summary="file_read success path=README.md",
            args_fingerprint="abc",
            result_fingerprint="def",
            trace_id="run_test",
        )
    )
    assert saved.receipt_id == "trc_test0001"
    assert path.exists()

    reloaded = ToolReceiptLedger(path).load()
    assert len(reloaded) == 1
    assert reloaded[0].operation == "file_read"
    assert ledger.get("trc_test0001") is not None
    assert ledger.list_by_operation("file_read")[0].receipt_id == "trc_test0001"
    assert ledger.list_by_trace("run_test")[0].receipt_id == "trc_test0001"


def test_invoke_file_write_skips_receipt_on_error(workspace: Path) -> None:
    token = bind_receipt_context(trace_id="t_err", path="repl", workspace=workspace)
    try:
        tool = FileWriteTool(workspace_root=workspace)
        call = ToolCall(
            action_id="a5",
            tool_name="file_write",
            arguments={"path": "", "content": "x"},
        )
        result = tool.invoke(call)
    finally:
        reset_receipt_context(token)

    assert result.status == "error"
    assert not default_receipts_path(workspace).exists()


@pytest.mark.parametrize("receipt_path", ["runtime", "daemon"])
def test_autonomous_runtime_tests_task_emits_receipt(
    workspace: Path,
    receipt_path: str,
) -> None:
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _runtime_agent(workspace)
    expected_trace = agent.log.trace_id

    report = AutonomousRuntime(
        agent,
        workspace=workspace,
        receipt_path=receipt_path,  # type: ignore[arg-type]
    ).run(
        AutonomousRuntimeConfig(
            limit=3,
            include_tests=True,
            enable_reflection=False,
        )
    )

    tests_report = next(r for r in report.tasks if r.task.kind == "tests")
    assert tests_report.status == "done"

    receipts = ToolReceiptLedger(default_receipts_path(workspace)).load()
    run_tests_receipts = [r for r in receipts if r.operation == "run_tests"]
    assert len(run_tests_receipts) == 1

    receipt = run_tests_receipts[0]
    assert receipt.operation == "run_tests"
    assert receipt.path == receipt_path
    assert receipt.trace_id == expected_trace
    assert receipt.trace_id
    assert default_receipts_path(workspace).exists()
