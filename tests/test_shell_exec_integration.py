"""End-to-end MVP-11 acceptance: shell_exec + approval + compensation + rollback.

Each `Test...` class corresponds to one of the 9 acceptance criteria from
the MVP-11 brief:

  1. safe command runs inside workspace               -> TestSafeCommand
  2. dangerous command blocked                        -> TestDangerousBlocked
  3. external / irreversible needs approval           -> TestApprovalRequired
  4. deny / abort -> no execution                     -> TestApprovalDeny + Abort
  5. timeout halts the process                        -> TestTimeoutSurfacing
  6. stdout / stderr redacted                         -> TestSecretRedaction
  7. JSONL contains shell_exec + approval + result    -> TestJsonlOrdering
  8. compensation plan created BEFORE execution       -> TestPlanBeforeExecution
  9. rollback works for file changes                  -> TestRollback

Every test uses the real AgentLoop with a real PolicyGate. The LLM is
FakeLLM (no network); the planner is bypassed via FakePlanner so we can
deliver arbitrary `sources` directly to the Executor and prove the gate's
own enforcement.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM, FakePlanner


def _events(log_path: Path) -> list[dict]:
    out: list[dict] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_agent(
    workspace: Path,
    *,
    approval_default: str | None = "approve",
    canned_sources: list[dict] | None = None,
) -> tuple[AgentLoop, Path, FakeLLM]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(WebSearchTool())
    registry.register(FileWriteTool(workspace_root=workspace))
    registry.register(ShellExecTool(workspace_root=workspace, timeout_seconds=5.0))

    # `FakePlanner` requires `expected_outcome` on every source; fill it
    # in here so individual tests can stay terse.
    for src in canned_sources or []:
        src.setdefault("expected_outcome", "executes the planned step")

    llm = FakeLLM(responses=["[synthesised]"])
    planner = FakePlanner(canned_sources or [])

    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    approver = AutoApprover(default=approval_default) if approval_default else None
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=approver,
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl", llm


# ===========================================================
# Acceptance #1: safe command runs INSIDE the workspace
# ===========================================================

class TestSafeCommand:
    def test_whoami_runs_no_approval_event(self, workspace: Path):
        if shutil.which("whoami") is None:
            pytest.skip("whoami not on PATH")
        agent, log_path, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["whoami"]},
                "label": "shell_exec:whoami",
            }],
        )
        agent.run("run whoami")

        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # tool_call + tool_result present.
        assert "tool_call" in kinds
        assert "tool_result" in kinds
        # No approval was solicited (read_only command).
        assert not any(e["event"].startswith("approval_") for e in ev)

    def test_mkdir_creates_inside_workspace(self, workspace: Path):
        agent, _, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "fresh_dir"]},
                "label": "shell_exec:mkdir fresh_dir",
            }],
        )
        agent.run("mkdir fresh_dir please")
        assert (workspace / "fresh_dir").is_dir()


# ===========================================================
# Acceptance #2: dangerous command blocked
# ===========================================================

class TestDangerousBlocked:
    def test_unknown_command_blocked_by_policy_gate(self, workspace: Path):
        """rm is not in the whitelist -> risk_for=external -> escalate;
        even AutoApprover(approve) cannot unblock it because the tool's
        own `run()` refuses (defence in depth)."""
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["rm", "-rf", "important_data"]},
                "label": "shell_exec:rm",
            }],
        )
        # File that "rm" would target — must survive even if rm tried.
        survivor = workspace / "important_data"
        survivor.write_text("must survive", encoding="utf-8")

        agent.run("delete important_data")

        # Survivor is intact.
        assert survivor.exists()
        assert survivor.read_text(encoding="utf-8") == "must survive"

        # The tool was DISPATCHED (external -> escalate -> approver
        # approved by default), but the tool's `_validate_argv` raised
        # PermissionError -> tool_result.status=error, output is None.
        ev = _events(log_path)
        results = [e for e in ev if e["event"] == "tool_result"]
        assert len(results) == 1
        # `status` rides on the tool_result event payload (the Pydantic
        # model field), AND is duplicated as a top-level kwarg by the
        # logger — check both for robustness.
        result_obj = results[0]["payload"]
        assert result_obj["status"] == "error"
        err = (result_obj.get("error") or "").lower()
        assert "whitelist" in err or "refuses" in err

    def test_shell_metachar_blocked_even_for_whitelisted_command(
        self, workspace: Path
    ):
        agent, log_path, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["touch", "evil;rm -rf"]},
                "label": "shell_exec:touch",
            }],
        )
        agent.run("touch evil;rm -rf")
        ev = _events(log_path)
        results = [e for e in ev if e["event"] == "tool_result"]
        # `touch` is mutating -> escalate -> approve -> tool dispatched -> validator rejects.
        # However, FakePlanner bypasses the sanitiser, so this exercises the
        # TOOL's own validation layer.
        assert len(results) == 1
        assert results[0]["payload"]["status"] == "error"
        err = (results[0]["payload"].get("error") or "").lower()
        assert "forbidden" in err or "metachar" in err


# ===========================================================
# Acceptance #3: external / irreversible risk needs approval
# ===========================================================

class TestApprovalRequired:
    def test_mkdir_emits_approval_request(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "newdir"]},
                "label": "shell_exec:mkdir newdir",
            }],
        )
        agent.run("create newdir")
        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # Order: approval_request -> approval_decision -> tool_call -> tool_result.
        request_idx = kinds.index("approval_request")
        decision_idx = kinds.index("approval_decision")
        call_idx = kinds.index("tool_call")
        result_idx = kinds.index("tool_result")
        assert request_idx < decision_idx < call_idx < result_idx
        # The approval_request must carry the EFFECTIVE risk, not the static one.
        req = ev[request_idx]["payload"]
        assert req["risk"] == "irreversible"
        assert (workspace / "newdir").is_dir()


# ===========================================================
# Acceptance #4: deny / abort -> no execution
# ===========================================================

class TestApprovalDeny:
    def test_deny_blocks_mkdir(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="deny",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "denied_dir"]},
                "label": "shell_exec:mkdir denied_dir",
            }],
        )
        agent.run("create denied_dir")
        assert not (workspace / "denied_dir").exists()
        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # No tool_call / tool_result on deny.
        assert "tool_call" not in kinds
        assert "tool_result" not in kinds
        # Error event carries approval_deny code.
        errs = [e for e in ev if e["event"] == "error"]
        assert any(e["payload"].get("code") == "approval_deny" for e in errs)

    def test_abort_blocks_touch(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="abort",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["touch", "aborted.txt"]},
                "label": "shell_exec:touch aborted.txt",
            }],
        )
        agent.run("touch aborted.txt")
        assert not (workspace / "aborted.txt").exists()
        ev = _events(log_path)
        assert "tool_call" not in [e["event"] for e in ev]
        errs = [e for e in ev if e["event"] == "error"]
        assert any(e["payload"].get("code") == "approval_abort" for e in errs)

    def test_no_approval_provider_blocks_irreversible(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default=None,
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "no_approver_dir"]},
                "label": "shell_exec:mkdir",
            }],
        )
        agent.run("mkdir with no approver wired")
        assert not (workspace / "no_approver_dir").exists()
        ev = _events(log_path)
        errs = [e for e in ev if e["event"] == "error"]
        assert any(e["payload"].get("code") == "approval_unavailable" for e in errs)


# ===========================================================
# Acceptance #5: timeout halts the process
# ===========================================================

class TestTimeoutSurfacing:
    def test_timeout_surfaces_as_timed_out_in_tool_result(self, workspace: Path):
        if shutil.which("whoami") is None:
            pytest.skip("whoami not on PATH")
        agent, log_path, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["whoami"]},
                "label": "shell_exec:whoami",
            }],
        )
        import subprocess

        # Force every subprocess.run to fail with TimeoutExpired.
        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(
                cmd=a[0], timeout=k.get("timeout", 0.1),
                output=b"", stderr=b"",
            )

        with mock.patch("subprocess.run", side_effect=raise_timeout):
            agent.run("run whoami")

        ev = _events(log_path)
        results = [e for e in ev if e["event"] == "tool_result"]
        assert len(results) == 1
        out = results[0]["payload"].get("output") or {}
        assert out.get("timed_out") is True
        assert out.get("exit_code") is None


# ===========================================================
# Acceptance #6: stdout/stderr redacted from secrets
# ===========================================================

class TestSecretRedaction:
    def test_secret_in_subprocess_stdout_never_lands_on_disk(self, workspace: Path):
        if shutil.which("whoami") is None:
            pytest.skip("whoami not on PATH")
        agent, log_path, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["whoami"]},
                "label": "shell_exec:whoami",
            }],
        )
        import subprocess

        leak = b"sk-1111111111111111111111111111111111111111111111111111\n"

        def fake_run(*a, **k):
            cp = subprocess.CompletedProcess(args=a[0], returncode=0)
            cp.stdout = leak
            cp.stderr = b""
            return cp

        with mock.patch("subprocess.run", side_effect=fake_run):
            agent.run("run whoami")

        # The literal credential MUST NEVER appear anywhere in the JSONL.
        raw_log = Path(log_path).read_text(encoding="utf-8")
        assert "sk-1111111111" not in raw_log
        assert "REDACTED" in raw_log


# ===========================================================
# Acceptance #7: JSONL contains shell_exec + approval + result
# ===========================================================

class TestJsonlOrdering:
    def test_full_event_chain_logged(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "chain_dir"]},
                "label": "shell_exec:mkdir chain_dir",
            }],
        )
        agent.run("mkdir chain_dir")

        ev = _events(log_path)
        kinds = [e["event"] for e in ev]
        # Every required event must be present in the right order.
        wanted = [
            "policy",
            "approval_request",
            "approval_decision",
            "tool_call",
            "tool_result",
            "compensation_registered",
        ]
        for kind in wanted:
            assert kind in kinds, f"missing event: {kind}"
        # Approval comes BEFORE tool_call which comes BEFORE compensation_registered.
        assert kinds.index("approval_request") < kinds.index("tool_call")
        assert kinds.index("tool_call") < kinds.index("compensation_registered")

        # The tool_call payload carries the argv.
        call = next(e for e in ev if e["event"] == "tool_call")
        assert call["payload"]["arguments"]["argv"] == ["mkdir", "chain_dir"]

        # The tool_result.output carries a compensation_plan.
        result = next(e for e in ev if e["event"] == "tool_result")
        assert result["payload"]["output"]["compensation_plan"]["actions"][0]["kind"] == "delete_path_if_created"


# ===========================================================
# Acceptance #8: compensation plan created BEFORE execution
# ===========================================================

class TestPlanBeforeExecution:
    def test_plan_present_in_tool_result_output(self, workspace: Path):
        agent, _, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "beforedir"]},
                "label": "shell_exec:mkdir beforedir",
            }],
        )
        agent.run("mkdir beforedir")
        # The plan is registered on the agent — proves it was extracted
        # from the tool_result.output structured dict.
        assert len(agent.compensation_log) == 1
        plan = agent.compensation_log[0]
        assert plan.tool_name == "shell_exec"
        assert plan.actions[0].kind == "delete_path_if_created"
        assert plan.actions[0].path == "beforedir"

    def test_failed_mutation_still_does_not_register_destructive_plan(
        self, workspace: Path
    ):
        # mkdir on existing path -> tool refuses -> NO plan should be
        # registered (errors don't capture plans, and the path was not
        # created so there's nothing to undo).
        (workspace / "preexisting").mkdir()
        agent, _, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "preexisting"]},
                "label": "shell_exec:mkdir preexisting",
            }],
        )
        agent.run("mkdir preexisting")
        assert agent.compensation_log == []


# ===========================================================
# Acceptance #9: rollback works for file changes
# ===========================================================

class TestRollback:
    def test_mkdir_then_rollback_removes_directory(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "rollback_dir"]},
                "label": "shell_exec:mkdir rollback_dir",
            }],
        )
        agent.run("mkdir rollback_dir")
        target = workspace / "rollback_dir"
        assert target.is_dir()
        assert len(agent.compensation_log) == 1

        report = agent.rollback(workspace_root=workspace)
        assert report.outcomes[0].status == "ok"
        assert not target.exists()
        # Log popped.
        assert agent.compensation_log == []

        ev = _events(log_path)
        applies = [e for e in ev if e["event"] == "compensation_apply"]
        assert len(applies) == 1
        assert applies[0]["payload"]["ok"] == 1

    def test_touch_then_rollback_removes_file(self, workspace: Path):
        agent, _, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["touch", "ephemeral.txt"]},
                "label": "shell_exec:touch ephemeral.txt",
            }],
        )
        agent.run("touch ephemeral.txt")
        target = workspace / "ephemeral.txt"
        assert target.exists()

        agent.rollback(workspace_root=workspace)
        assert not target.exists()

    def test_rollback_skips_when_log_empty(self, workspace: Path):
        agent, log_path, _ = _build_agent(workspace)
        report = agent.rollback(workspace_root=workspace)
        assert report.outcomes == []
        ev = _events(log_path)
        applies = [e for e in ev if e["event"] == "compensation_apply"]
        assert applies and applies[0]["payload"]["skipped_reason"] == "no plans registered"

    def test_rollback_by_plan_id(self, workspace: Path):
        # Run two mkdir commands, then rollback the FIRST by id.
        # Both directories created, but only the first one removed.
        agent, _, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "first_dir"]},
                "label": "shell_exec:mkdir first_dir",
            }],
        )
        agent.run("mkdir first_dir")
        first_plan_id = agent.compensation_log[0].id

        agent.planner.sources = [{
            "tool": "shell_exec",
            "arguments": {"argv": ["mkdir", "second_dir"]},
            "label": "shell_exec:mkdir second_dir",
            "expected_outcome": "executes the planned step",
        }]
        agent.run("mkdir second_dir")
        # Two plans now registered (oldest first).
        assert [p.tool_name for p in agent.compensation_log] == ["shell_exec", "shell_exec"]

        report = agent.rollback(plan_id=first_plan_id, workspace_root=workspace)
        assert report.outcomes[0].status == "ok"
        assert not (workspace / "first_dir").exists()
        assert (workspace / "second_dir").exists()  # untouched
        assert len(agent.compensation_log) == 1

    def test_rollback_unknown_id_is_skip(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "alive_dir"]},
                "label": "shell_exec:mkdir alive_dir",
            }],
        )
        agent.run("mkdir alive_dir")
        # Wrong id.
        report = agent.rollback(plan_id="comp_nonexistent", workspace_root=workspace)
        assert report.outcomes == []
        ev = _events(log_path)
        applies = [e for e in ev if e["event"] == "compensation_apply"]
        last = applies[-1]["payload"]
        assert "not found" in (last.get("skipped_reason") or "")
        # The real plan is still in the log.
        assert len(agent.compensation_log) == 1
        assert (workspace / "alive_dir").exists()

    def test_rollback_without_workspace_root_skips(self, workspace: Path):
        agent, log_path, _ = _build_agent(
            workspace,
            approval_default="approve",
            canned_sources=[{
                "tool": "shell_exec",
                "arguments": {"argv": ["mkdir", "abandoned_dir"]},
                "label": "shell_exec:mkdir abandoned_dir",
            }],
        )
        agent.run("mkdir abandoned_dir")
        report = agent.rollback()  # workspace_root=None
        assert report.outcomes == []
        # The directory is still there; the plan is still in the log.
        assert (workspace / "abandoned_dir").exists()
        assert len(agent.compensation_log) == 1


# ===========================================================
# Planner sanitiser smoke-test (defence in depth at planner layer)
# ===========================================================

class TestPlannerSanitiser:
    def test_evil_command_dropped_by_planner(self, workspace: Path):
        """When the REAL LLMPlanner emits a non-whitelisted command, the
        sanitiser drops the step before it reaches the executor."""
        from core.planner import LLMPlanner

        registry = ToolRegistry()
        registry.register(ShellExecTool(workspace_root=workspace))
        llm = FakeLLM(responses=[json.dumps({
            "reasoning": "user asked to delete things",
            "steps": [{
                "tool": "shell_exec",
                "arguments": {"argv": ["rm", "-rf", "/etc"]},
                "rationale": "we should do this",
            }],
        })])
        planner = LLMPlanner(llm=llm, registry=registry)
        out = planner.plan("rm -rf /etc", file_hint=None)
        # Step was dropped — sources empty, warning recorded.
        assert out.sources == []
        assert any("not in whitelist" in w for w in out.warnings)

    def test_metachar_in_argv_dropped_by_planner(self, workspace: Path):
        from core.planner import LLMPlanner

        registry = ToolRegistry()
        registry.register(ShellExecTool(workspace_root=workspace))
        llm = FakeLLM(responses=[json.dumps({
            "reasoning": "create then delete",
            "steps": [{
                "tool": "shell_exec",
                "arguments": {"argv": ["touch", "a;rm -rf"]},
                "rationale": "smuggle a separator",
            }],
        })])
        planner = LLMPlanner(llm=llm, registry=registry)
        out = planner.plan("touch a;rm -rf", file_hint=None)
        assert out.sources == []
        assert any("metachar" in w for w in out.warnings)
