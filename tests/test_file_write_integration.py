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

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool
from tests.conftest import FakeLLM, FakePlanner


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
# (1) Create new file inside workspace works
# ============================================================

class TestCreateInsideWorkspace:
    def test_new_file_create_runs_without_approval(self, workspace: Path):
        approver = AutoApprover(default="deny")  # would block irreversible
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=approver,
            planned_args={"path": "out/created.txt", "content": "hello"},
        )

        agent.run(user_question="save it", file_hint=None)

        # File exists with the expected content.
        target = workspace / "out" / "created.txt"
        assert target.read_text(encoding="utf-8") == "hello"

        # Approval was NOT asked — risk_for downgraded to reversible.
        assert approver.calls == [], "create must not escalate"

        # Audit log shape.
        events = _events(log_path)
        names = [e["event"] for e in events]
        assert "policy" in names
        assert "tool_call" in names
        assert "tool_result" in names
        assert "verify" in names
        assert "approval_request" not in names
        assert "approval_decision" not in names

        # Policy event carries the dynamic risk reason.
        policy = next(e for e in events if e["event"] == "policy")
        assert policy["payload"]["decision"] == "allow"
        assert any("reversible" in r for r in policy["payload"]["reasons"])


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
# (3, 5, 6, 8) Overwrite requires approval — approve flow
# ============================================================

class TestOverwriteApproveFlow:
    def test_approve_overwrites_file_and_creates_backup(self, workspace: Path):
        target = workspace / "doc.txt"
        target.write_text("original\n", encoding="utf-8")

        approver = AutoApprover(default="approve", reason="user approved")
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=approver,
            planned_args={"path": "doc.txt", "content": "updated\n"},
        )

        agent.run(user_question="overwrite", file_hint=None)

        # (5) Approve -> file changed.
        assert target.read_text(encoding="utf-8") == "updated\n"
        # (6) Backup exists with the original content.
        backups = list(workspace.glob("doc.txt.bak.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "original\n"

        # (3) Approval was actually consulted.
        assert len(approver.calls) == 1
        request = approver.calls[0]
        assert request.tool_name == "file_write"
        assert request.risk == "irreversible"
        assert request.arguments["path"] == "doc.txt"

        # (8) JSONL carries approval_request, approval_decision, tool_result.
        events = _events(log_path)
        names = [e["event"] for e in events]
        assert "approval_request" in names
        assert "approval_decision" in names
        assert "tool_call" in names
        assert "tool_result" in names

        # The tool_result also includes the backup_path so the synthesizer
        # can surface it to the user.
        tr = next(e for e in events if e["event"] == "tool_result")
        out = tr["payload"]["output"]
        assert out["mode"] == "overwrite"
        assert out["backup_path"] is not None


# ============================================================
# (4) Deny / abort -> file not changed
# ============================================================

class TestOverwriteDenyAbortFlow:
    @pytest.mark.parametrize("verdict", ["deny", "abort"])
    def test_deny_or_abort_keeps_file_intact(self, workspace: Path, verdict):
        target = workspace / "doc.txt"
        target.write_text("ORIGINAL\n", encoding="utf-8")

        approver = AutoApprover(default=verdict, reason=f"user said {verdict}")
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=approver,
            planned_args={"path": "doc.txt", "content": "NEW\n"},
        )
        agent.run(user_question="overwrite", file_hint=None)

        # File untouched.
        assert target.read_text(encoding="utf-8") == "ORIGINAL\n"
        # No backup was created either (we never reached run()).
        assert list(workspace.glob("doc.txt.bak.*")) == []

        events = _events(log_path)
        names = [e["event"] for e in events]
        assert "approval_request" in names
        assert "approval_decision" in names
        # Critically: tool_call / tool_result must NOT appear — the tool
        # was never invoked.
        assert "tool_call" not in names
        assert "tool_result" not in names

        # The approval_decision event records the verdict verbatim.
        dec = next(e for e in events if e["event"] == "approval_decision")
        assert dec["payload"]["decision"] == verdict


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
# (9) Without an approval provider, overwrite is impossible
# ============================================================

class TestNoApprovalProvider:
    def test_overwrite_without_provider_does_not_run_tool(self, workspace: Path):
        target = workspace / "doc.txt"
        target.write_text("PROTECTED\n", encoding="utf-8")

        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=None,   # no provider wired
            planned_args={"path": "doc.txt", "content": "BAD\n"},
        )
        agent.run(user_question="overwrite", file_hint=None)

        # Original content preserved.
        assert target.read_text(encoding="utf-8") == "PROTECTED\n"
        assert list(workspace.glob("doc.txt.bak.*")) == []

        events = _events(log_path)
        names = [e["event"] for e in events]
        # Loop emits an `error` event with the documented code.
        errors = [e for e in events if e["event"] == "error"]
        assert any(e["payload"]["code"] == "approval_unavailable" for e in errors), \
            f"expected approval_unavailable in {[e['payload'].get('code') for e in errors]}"

        # Tool MUST NOT have been invoked.
        assert "tool_call" not in names
        assert "tool_result" not in names


# ============================================================
# Bonus: create still works even without provider (orthogonal proof)
# ============================================================

class TestCreateWorksWithNoProvider:
    def test_create_does_not_need_provider(self, workspace: Path):
        agent, _, _ = _build_loop(
            workspace,
            approval_provider=None,
            planned_args={"path": "fresh.txt", "content": "hi"},
        )
        agent.run(user_question="create", file_hint=None)

        # Reversible classification let it through.
        assert (workspace / "fresh.txt").read_text(encoding="utf-8") == "hi"


# ============================================================
# MVP-11: file_write feeds the compensation registry; :rollback
# both DELETES newly-created files and RESTORES overwritten ones.
# ============================================================

class TestFileWriteCompensation:
    def test_create_then_rollback_deletes_file(self, workspace: Path):
        agent, log_path, _ = _build_loop(
            workspace,
            approval_provider=AutoApprover(default="approve"),
            planned_args={"path": "rollback_create.txt", "content": "ephemeral"},
        )
        agent.run(user_question="create rollback_create.txt", file_hint=None)
        target = workspace / "rollback_create.txt"
        assert target.exists()
        # Tool emitted a non-noop plan AND AgentLoop captured it.
        assert len(agent.compensation_log) == 1
        plan = agent.compensation_log[0]
        assert plan.tool_name == "file_write"
        assert plan.actions[0].kind == "delete_path_if_created"
        assert plan.actions[0].path == "rollback_create.txt"

        # Audit event present.
        events = _events(log_path)
        assert any(e["event"] == "compensation_registered" for e in events)

        report = agent.rollback(workspace_root=workspace)
        assert report.outcomes[0].status == "ok"
        assert not target.exists()
        assert agent.compensation_log == []

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

    def test_two_creates_then_lifo_rollback_pops_latest(self, workspace: Path):
        """Two successive file_write create calls register two plans;
        `:rollback` (no plan_id) must remove the latest first."""
        # First create
        agent, _, _ = _build_loop(
            workspace,
            approval_provider=AutoApprover(default="approve"),
            planned_args={"path": "a.txt", "content": "A"},
        )
        agent.run(user_question="save a.txt", file_hint=None)
        # Swap in a new step and run again on the SAME agent.
        agent.planner.sources = [{
            "tool": "file_write",
            "arguments": {"path": "b.txt", "content": "B"},
            "label": "file_write:b.txt",
            "expected_outcome": "create b",
        }]
        agent.run(user_question="save b.txt", file_hint=None)
        assert (workspace / "a.txt").exists()
        assert (workspace / "b.txt").exists()
        assert len(agent.compensation_log) == 2

        # LIFO pop -> removes b.txt only.
        agent.rollback(workspace_root=workspace)
        assert (workspace / "a.txt").exists()
        assert not (workspace / "b.txt").exists()
        assert len(agent.compensation_log) == 1

        # One more rollback removes a.txt.
        agent.rollback(workspace_root=workspace)
        assert not (workspace / "a.txt").exists()
        assert agent.compensation_log == []

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
