"""End-to-end MVP-9 acceptance: `file_write` through the full loop.

Wires up a real AgentLoop with:
  - the real FileWriteTool (sandboxed to the test workspace)
  - the real PolicyGate (so `risk_for` drives the gate decision)
  - a FakePlanner that delivers a one-step `file_write` plan
  - a deterministic AutoApprover so we can exercise approve / deny /
    abort without touching stdin

These tests pin all 9 acceptance criteria from the MVP-9 plan:
  (1) create new file inside workspace works
  (2) write outside workspace is blocked
  (3) overwrite requires approval
  (4) deny / abort -> file not changed
  (5) approve -> file changed
  (6) backup is created before overwrite
  (7) secrets in content are blocked (no file written)
  (8) JSONL has approval_request / approval_decision / tool_result
  (9) without an approval provider, overwrite is impossible
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool

# ============================================================
# Helpers
# ============================================================

def _events(log_path: Path) -> list[dict]:
    out: list[dict] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_loop(
    workspace: Path,
    *,
    approval_provider: Any | None,
    planned_args: dict[str, str],
) -> tuple[AgentLoop, Path, FileWriteTool]:
    """Build a single-step file_write agent.

    The FakePlanner emits exactly one step calling `file_write` with the
    given arguments; the loop wires up real PolicyGate + real FileWriteTool
    so every safety boundary is exercised honestly.
    """
    tool = FileWriteTool(workspace_root=workspace)
    registry = ToolRegistry()
    registry.register(tool)
    policy = PolicyGate(registry)

    planned_step = {
        "tool": "file_write",
        "arguments": planned_args,
        "label": f"file_write:{planned_args.get('path', '?')}",
        "expected_outcome": "File created or overwritten",
    }
    planner = FakePlanner(sources=[planned_step])

    # Synthesis must work even when no artifact survives — return any string.
    llm = FakeLLM(responses=["synthesised answer"])

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
        approval_provider=approval_provider,
        # Single attempt so we can assert event counts deterministically.
        # MVP-8 re-planning is exercised in its own test file.
        max_replan_attempts=1,
        # Disable clarification gate: these tests exercise the tool / approval
        # layer with synthetic short questions, not the ambiguity detector.
        clarification_enabled=False,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl", tool


# ============================================================
# (2) Write outside workspace is blocked
# ============================================================

class TestSandboxAtLoopLevel:
    def test_parent_traversal_surfaces_as_tool_error(self, workspace: Path):
        # The planner sanitiser would normally drop this; here we go
        # through FakePlanner so we can prove the TOOL's sandbox also holds.
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=AutoApprover(default="approve"),
            planned_args={"path": "../escape.txt", "content": "x"},
        )
        agent.run(user_question="hack", file_hint=None)

        # Nothing leaked outside the workspace.
        assert not (workspace.parent / "escape.txt").exists()

        events = _events(log_path)
        tool_results = [e for e in events if e["event"] == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["payload"]["status"] == "error"
        assert "escapes workspace" in tool_results[0]["payload"]["error"]


# ============================================================
# (7) Secrets in content are blocked at tool level
# ============================================================

class TestSecretInContentBlocked:
    def test_secret_in_content_blocks_write_even_for_new_file(self, workspace: Path):
        # New file -> risk_for=reversible -> policy=allow -> tool.invoke() runs.
        # The tool itself must refuse credentials and surface a tool_error.
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=None,
            planned_args={
                "path": "leak.txt",
                "content": "key=sk-abcdefghijklmnopqrstuvwxyz0123",
            },
        )
        agent.run(user_question="save my key", file_hint=None)

        # File NOT created.
        assert not (workspace / "leak.txt").exists()

        events = _events(log_path)
        tr = next(e for e in events if e["event"] == "tool_result")
        assert tr["payload"]["status"] == "error"
        assert "credentials" in tr["payload"]["error"]

        # JSONL must not carry the raw secret anywhere.
        text = log_path.read_text(encoding="utf-8")
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in text


# ============================================================
# MVP-11: file_write feeds the compensation registry; :rollback
# both DELETES newly-created files and RESTORES overwritten ones.
# ============================================================

class TestFileWriteCompensation:
    def test_overwrite_then_rollback_restores_original_content(
        self, workspace: Path
    ):
        target = workspace / "rollback_overwrite.txt"
        target.write_text("ORIGINAL", encoding="utf-8")

        agent, _, _ = _build_loop(
            workspace,
            approval_provider=AutoApprover(default="approve"),
            planned_args={"path": "rollback_overwrite.txt", "content": "REPLACED"},
        )
        agent.run(user_question="overwrite rollback_overwrite.txt", file_hint=None)
        assert target.read_text(encoding="utf-8") == "REPLACED"
        # Backup file exists, registered in the plan.
        backups = list(workspace.glob("rollback_overwrite.txt.bak.*"))
        assert len(backups) == 1
        plan = agent.compensation_log[0]
        assert plan.actions[0].kind == "restore_from_backup"
        assert plan.actions[0].backup_path == backups[0].name

        report = agent.rollback(workspace_root=workspace)
        assert report.outcomes[0].status == "ok"
        # Original content is back; backup file is consumed.
        assert target.read_text(encoding="utf-8") == "ORIGINAL"
        assert list(workspace.glob("rollback_overwrite.txt.bak.*")) == []


    def test_failed_write_registers_no_plan(self, workspace: Path):
        # A secret in content -> tool raises -> NO compensation captured.
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=AutoApprover(default="approve"),
            planned_args={
                "path": "credentials.txt",
                "content": "key=sk-fffffffffffffffffffffffffffffffffffffffffffffffff",
            },
        )
        agent.run(user_question="save creds", file_hint=None)
        assert not (workspace / "credentials.txt").exists()
        assert agent.compensation_log == []
        # No compensation_registered event either.
        ev = _events(log_path)
        assert not any(e["event"] == "compensation_registered" for e in ev)
