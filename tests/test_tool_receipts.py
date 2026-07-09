"""Tool receipts slice 1a — ledger, redaction, and Tool.invoke hook."""
from __future__ import annotations

import json
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
    SLICE_1A_RECEIPT_TOOLS,
    SLICE_G5B_EFFECT_RECEIPT_TOOLS,
    ToolReceipt,
    ToolReceiptLedger,
    append_receipt,
    bind_receipt_context,
    default_receipts_path,
    record_tool_invoke_receipt,
    reset_receipt_context,
)
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_PII_EMAIL = "client@example.com"


class _EchoTool(Tool):
    name = "file_read"
    description = "echo stub"
    risk = "read_only"

    def run(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs


class _UnknownStub(Tool):
    name = "spawn_subagent"
    description = "not in receipt slices"
    risk = "reversible"

    def run(self, **kwargs):  # type: ignore[no-untyped-def]
        return "ok"


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


def test_slice_1a_tool_set_matches_proposal() -> None:
    assert "file_read" in SLICE_1A_RECEIPT_TOOLS
    assert "run_tests" in SLICE_1A_RECEIPT_TOOLS
    assert "web_fetch" in SLICE_1A_RECEIPT_TOOLS
    assert "file_write" not in SLICE_1A_RECEIPT_TOOLS


def test_slice_g5b_effect_receipt_tools() -> None:
    assert SLICE_G5B_EFFECT_RECEIPT_TOOLS == frozenset({"file_write"})


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


def test_append_redacts_secret_in_summary_and_args(workspace: Path) -> None:
    token = bind_receipt_context(trace_id="t1", path="repl", workspace=workspace)
    try:
        receipt = append_receipt(
            ToolReceipt(
                receipt_id="trc_redact01",
                operation="shell_exec",
                status="success",
                summary=f"shell_exec success command=export KEY={_AWS_KEY}",
                args_fingerprint="unused",
                result_fingerprint="unused",
                refs={"note": _PII_EMAIL},
            ),
            workspace=workspace,
        )
    finally:
        reset_receipt_context(token)

    assert receipt is not None
    assert _AWS_KEY not in receipt.summary
    assert "[REDACTED:" in receipt.summary
    assert _PII_EMAIL not in json.dumps(receipt.refs)

    raw = default_receipts_path(workspace).read_text(encoding="utf-8")
    assert _AWS_KEY not in raw
    assert _PII_EMAIL not in raw


def test_record_tool_invoke_redacts_arguments(workspace: Path) -> None:
    token = bind_receipt_context(trace_id="trace_x", path="repl", workspace=workspace)
    try:
        tool = _EchoTool()
        call = ToolCall(
            action_id="act_1",
            tool_name="file_read",
            arguments={"path": "secrets.txt", "token": _AWS_KEY},
        )
        from core.models import ToolResult

        result = ToolResult(
            tool_call_id=call.id,
            status="success",
            output=f"contact {_PII_EMAIL}",
            latency_ms=1,
        )
        saved = record_tool_invoke_receipt(tool, call, result)
    finally:
        reset_receipt_context(token)

    assert saved is not None
    assert saved.operation == "file_read"
    assert saved.refs["tool_call_id"] == call.id
    assert _AWS_KEY not in default_receipts_path(workspace).read_text(encoding="utf-8")


def test_invoke_file_read_emits_receipt(workspace: Path) -> None:
    target = workspace / "hello.txt"
    target.write_text("hello", encoding="utf-8")

    token = bind_receipt_context(trace_id="tr_file", path="repl", workspace=workspace)
    try:
        tool = FileReadTool(workspace_root=workspace)
        call = ToolCall(
            action_id="a1",
            tool_name="file_read",
            arguments={"path": "hello.txt"},
        )
        result = tool.invoke(call)
    finally:
        reset_receipt_context(token)

    assert result.status == "success"
    receipts = ToolReceiptLedger(default_receipts_path(workspace)).load()
    assert len(receipts) == 1
    assert receipts[0].operation == "file_read"
    assert receipts[0].trace_id == "tr_file"
    assert receipts[0].refs["tool_call_id"] == call.id


def test_invoke_skips_non_receipt_tool(workspace: Path) -> None:
    token = bind_receipt_context(trace_id="t2", path="repl", workspace=workspace)
    try:
        tool = _UnknownStub()
        call = ToolCall(
            action_id="a2",
            tool_name="spawn_subagent",
            arguments={"role": "reviewer", "goal": "check"},
        )
        tool.invoke(call)
    finally:
        reset_receipt_context(token)

    assert not default_receipts_path(workspace).exists()


def test_invoke_file_write_emits_receipt_on_success(workspace: Path) -> None:
    token = bind_receipt_context(trace_id="tr_write", path="repl", workspace=workspace)
    try:
        tool = FileWriteTool(workspace_root=workspace)
        call = ToolCall(
            action_id="a3",
            tool_name="file_write",
            arguments={"path": "out.txt", "content": "hello world"},
        )
        result = tool.invoke(call)
    finally:
        reset_receipt_context(token)

    assert result.status == "success"
    receipts = ToolReceiptLedger(default_receipts_path(workspace)).load()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.operation == "file_write"
    assert receipt.status == "success"
    assert receipt.trace_id == "tr_write"
    assert receipt.refs["tool_call_id"] == call.id
    assert "path=out.txt" in receipt.summary
    assert "bytes=11" in receipt.summary
    assert "mode=create" in receipt.summary
    assert "hello world" not in receipt.summary


def test_invoke_file_write_redacts_secret_content(workspace: Path) -> None:
    secret_body = f"password={_AWS_KEY} contact {_PII_EMAIL}"
    token = bind_receipt_context(trace_id="tr_redact_write", path="repl", workspace=workspace)
    try:
        tool = FileWriteTool(workspace_root=workspace)
        call = ToolCall(
            action_id="a4",
            tool_name="file_write",
            arguments={"path": "secrets.txt", "content": secret_body},
        )
        # FileWriteTool refuses secrets — receipt must not be written on error.
        result = tool.invoke(call)
    finally:
        reset_receipt_context(token)

    assert result.status == "error"
    assert not default_receipts_path(workspace).exists()


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


def test_record_file_write_receipt_omits_raw_content(workspace: Path) -> None:
    from core.models import ToolResult

    token = bind_receipt_context(trace_id="tr_fp", path="runtime", workspace=workspace)
    try:
        tool = FileWriteTool(workspace_root=workspace)
        secret = f"token={_AWS_KEY} email={_PII_EMAIL}"
        call = ToolCall(
            action_id="a6",
            tool_name="file_write",
            arguments={"path": "note.txt", "content": secret},
        )
        result = ToolResult(
            tool_call_id=call.id,
            status="success",
            output={
                "path": "note.txt",
                "mode": "create",
                "bytes_written": len(secret.encode("utf-8")),
                "backup_path": None,
            },
            latency_ms=2,
        )
        saved = record_tool_invoke_receipt(tool, call, result)
    finally:
        reset_receipt_context(token)

    assert saved is not None
    raw = default_receipts_path(workspace).read_text(encoding="utf-8")
    assert _AWS_KEY not in raw
    assert _PII_EMAIL not in raw
    assert secret not in raw
    assert "bytes=" in saved.summary
    from core.models import ToolResult

    tool = _EchoTool()
    call = ToolCall(action_id="a", tool_name="file_read", arguments={"path": "x"})
    result = ToolResult(tool_call_id=call.id, status="success", output="ok", latency_ms=0)
    assert record_tool_invoke_receipt(tool, call, result) is None


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
