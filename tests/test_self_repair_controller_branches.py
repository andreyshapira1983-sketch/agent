"""Unit coverage for SelfRepairController defensive branches.

The end-to-end tests (`test_self_repair_e2e.py`) prove the happy path, the
approval-denial brake, the no-provider brake, the policy-block brake and the
rollback-on-red path. The branches below are the REMAINING load-bearing
guards that coverage flagged as never exercised:

  * governance veto on the write step (`_execute_tool` governance.denied),
  * a tool that fails outright (`tool_error`),
  * a tool whose output fails validation (`verify_failed`),
  * the regression-guard LESSON written after a confirmed repair.

These use a small FakeAgent so each branch can be triggered deterministically
without a real pytest subprocess or LLM. The agent surface mirrors exactly
what SelfRepairController touches: log / compensation_log / policy / registry /
_call_tool / rollback / approval_provider / remember / episodic_store.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.compensation import CompensationPlan
from core.governance import (
    AgentMode,
    GovernanceDecision,
    GovernancePolicy,
    GovernedOperation,
)
from core.models import Action, PolicyDecision, ToolResult
from core.self_repair import RepairProposal, SelfRepairController


# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #

class _FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def log(self, event: str, payload: Any = None, **_kw: Any) -> None:
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


class _FakeTool:
    """A tool whose output validation can be forced to fail."""

    def __init__(self, risk: str = "reversible", *, valid: bool = True) -> None:
        self._risk = risk
        self._valid = valid

    def validate_output(self, _output: Any) -> tuple[bool, list[str]]:
        return (True, []) if self._valid else (False, ["forced-invalid-output"])

    def risk_for(self, _params: dict[str, Any]) -> str:
        return self._risk


class _FakeRollbackReport:
    def __init__(self, errors: int = 0) -> None:
        self._errors = errors

    def summary(self) -> dict[str, Any]:
        return {"ok": self._errors == 0, "noop": False, "error": self._errors}


class _FakeAgent:
    """Minimal agent surface the SelfRepairController relies on.

    `tool_outputs` maps tool_name -> dict(output=..., status=..., valid=...).
    Defaults make every step succeed so a subclassed governance policy or a
    single overridden tool can isolate one branch.
    """

    def __init__(
        self,
        *,
        tool_outputs: dict[str, dict[str, Any]] | None = None,
        approval_default: str | None = "approve",
        with_memory: bool = False,
    ) -> None:
        self.log = _FakeLog()
        self.compensation_log: list[CompensationPlan] = []
        self.policy = self  # reuse self for .check()
        self.registry = self  # reuse self for .get()
        self.approval_provider = (
            AutoApprover(default=approval_default) if approval_default else None
        )
        self._tool_outputs = tool_outputs or {}
        self.rolled_back_plan_ids: list[str] = []
        # memory side
        self.remembered: list[dict[str, Any]] = []
        self.episodic_saved: list[Any] = []
        if with_memory:
            self.episodic_store = self  # reuse self for .save()
        # else: no episodic_store attribute at all

    # ---- policy.check ----
    def check(self, action: Action) -> PolicyDecision:
        return PolicyDecision(
            policy_id="fake",
            subject=action.tool_name,
            action="tool_call",
            decision="allow",
            reasons=["fake-allow"],
        )

    # ---- registry.get ----
    def get(self, tool_name: str) -> _FakeTool:
        spec = self._tool_outputs.get(tool_name, {})
        return _FakeTool(valid=spec.get("valid", True))

    # ---- _call_tool ----
    def _call_tool(self, action: Action) -> ToolResult:
        spec = self._tool_outputs.get(action.tool_name, {})
        # `outputs` (a list) lets a tool return different results on successive
        # calls — needed because baseline_tests and post_tests both call
        # run_tests but must report different pass counts.
        if "outputs" in spec and spec["outputs"]:
            current = spec["outputs"].pop(0)
        else:
            current = spec
        status = current.get("status", "success")
        if action.tool_name == "file_write" and status == "success":
            # A real write registers a compensation plan; mirror that so the
            # controller can locate the new plan id and roll back later.
            self.compensation_log.append(
                CompensationPlan(tool_name="file_write", description="undo write")
            )
        return ToolResult(
            tool_call_id="tc",
            status=status,
            output=current.get("output"),
            error=current.get("error"),
        )

    # ---- rollback ----
    def rollback(self, *, plan_id: str, workspace_root: Path) -> _FakeRollbackReport:
        self.rolled_back_plan_ids.append(plan_id)
        return _FakeRollbackReport(errors=0)

    # ---- memory ----
    def remember(self, **kwargs: Any) -> None:
        self.remembered.append(kwargs)

    def save(self, episode: Any) -> None:  # episodic_store.save
        self.episodic_saved.append(episode)


def _proposal(**kw: Any) -> RepairProposal:
    base = dict(
        path="core/example.py",
        proposed_content="# fixed content\n",
        reason="invert the broken guard",
        confidence=0.9,
        evidence=("tests/test_example.py::test_x failed",),
    )
    base.update(kw)
    return RepairProposal(**base)


# Defaults that drive a full SUCCESS: baseline verified (timed_out False),
# diff non-empty, post tests green and matching baseline pass count.
def _success_tool_outputs() -> dict[str, dict[str, Any]]:
    return {
        "run_tests": {
            "output": {
                "timed_out": False,
                "exit_code": 0,
                "passed": 5,
                "failed": 0,
                "errors": 0,
            }
        },
        "diff_file": {"output": {"additions": 1, "deletions": 1, "diff": "x"}},
        "file_write": {"output": {"path": "core/example.py", "bytes_written": 16}},
    }


# --------------------------------------------------------------------------- #
# governance veto on the write step                                             #
# --------------------------------------------------------------------------- #

class _DenyWriteGovernance(GovernancePolicy):
    """Allows reads/diff but vetoes the actual code-change write."""

    def evaluate(self, *, mode, operation, **kw) -> GovernanceDecision:  # type: ignore[override]
        op = operation if isinstance(operation, GovernedOperation) else GovernedOperation(operation)
        if op is GovernedOperation.APPLY_CODE_CHANGE:
            return GovernanceDecision(
                mode=AgentMode.REPAIR,
                operation=op,
                verdict="deny",
                reasons=("governance vetoes this code change",),
            )
        return super().evaluate(mode=mode, operation=operation, **kw)


def test_governance_veto_blocks_write_and_leaves_file_untouched(tmp_path: Path):
    agent = _FakeAgent(tool_outputs=_success_tool_outputs())
    controller = SelfRepairController(
        agent, workspace_root=tmp_path, governance_policy=_DenyWriteGovernance()
    )

    report = controller.run(_proposal())

    assert report.status == "blocked"
    # baseline + diff ran; the write was vetoed → no compensation registered.
    assert agent.compensation_log == []
    assert agent.rolled_back_plan_ids == []
    # An error was logged for the governance denial.
    assert any(
        e == "error" and getattr(p, "code", None) == "governance_denied"
        for e, p in agent.log.events
    )
    write_steps = [s for s in report.steps if s.name == "write"]
    assert write_steps and write_steps[0].status == "governance_denied"


# --------------------------------------------------------------------------- #
# tool that fails outright                                                      #
# --------------------------------------------------------------------------- #

def test_baseline_tool_error_aborts_repair_as_failed(tmp_path: Path):
    outputs = _success_tool_outputs()
    outputs["run_tests"] = {"status": "error", "error": "pytest crashed"}
    agent = _FakeAgent(tool_outputs=outputs)
    controller = SelfRepairController(agent, workspace_root=tmp_path)

    report = controller.run(_proposal())

    assert report.status == "failed"
    # Never got to write.
    assert agent.compensation_log == []
    baseline = [s for s in report.steps if s.name == "baseline_tests"]
    assert baseline and baseline[0].status == "tool_error"
    assert any(
        e == "error" and getattr(p, "code", None) == "tool_error"
        for e, p in agent.log.events
    )


# --------------------------------------------------------------------------- #
# tool whose output fails validation                                            #
# --------------------------------------------------------------------------- #

def test_baseline_verify_failure_aborts_repair_as_failed(tmp_path: Path):
    outputs = _success_tool_outputs()
    outputs["run_tests"]["valid"] = False  # validate_output → (False, [...])
    agent = _FakeAgent(tool_outputs=outputs)
    controller = SelfRepairController(agent, workspace_root=tmp_path)

    report = controller.run(_proposal())

    assert report.status == "failed"
    assert agent.compensation_log == []
    baseline = [s for s in report.steps if s.name == "baseline_tests"]
    assert baseline and baseline[0].status == "verify_failed"
    assert any(
        e == "error" and getattr(p, "code", None) == "verify_failed"
        for e, p in agent.log.events
    )


# --------------------------------------------------------------------------- #
# regression-guard LESSON after a confirmed repair                              #
# --------------------------------------------------------------------------- #

def test_successful_repair_writes_regression_guard_lesson(tmp_path: Path):
    agent = _FakeAgent(tool_outputs=_success_tool_outputs(), with_memory=True)
    controller = SelfRepairController(agent, workspace_root=tmp_path)

    report = controller.run(_proposal(path="core/widget.py", reason="off-by-one"))

    assert report.status == "repaired"
    # The lesson reached BOTH long-term memory and the episodic store.
    assert len(agent.remembered) == 1
    lesson = agent.remembered[0]
    assert "core/widget.py" in lesson["content"]
    assert "off-by-one" in lesson["content"]
    assert "regression-guard" in lesson["tags"]
    assert lesson["source"] == "repair"
    assert len(agent.episodic_saved) == 1
    assert any(e == "repair_lesson_saved" for e, _ in agent.log.events)


def test_successful_repair_without_memory_does_not_crash(tmp_path: Path):
    # No episodic_store, no remember side effects expected — the lesson writer
    # must never abort the repair report flow.
    agent = _FakeAgent(tool_outputs=_success_tool_outputs(), with_memory=False)
    controller = SelfRepairController(agent, workspace_root=tmp_path)

    report = controller.run(_proposal())

    assert report.status == "repaired"
    # remember() still exists on the fake agent, so the lesson is recorded
    # there, but no episodic_store save happened.
    assert agent.episodic_saved == []


def test_lesson_save_failure_is_logged_not_silent(tmp_path: Path):
    # A repaired fix already landed; if persisting the LESSON raises, the
    # controller must NOT crash the repair — but the failure must be SURFACED
    # (repair_lesson_save_failed), never swallowed silently. Regression for the
    # trace where the agent flagged a `try/except: pass` around the repair
    # lesson write as a silent-memory-loss risk.
    agent = _FakeAgent(tool_outputs=_success_tool_outputs(), with_memory=True)

    def _boom(episode: Any) -> None:
        raise RuntimeError("episodic store unavailable")

    agent.save = _boom  # type: ignore[assignment]

    controller = SelfRepairController(agent, workspace_root=tmp_path)
    report = controller.run(_proposal(path="core/widget.py", reason="off-by-one"))

    # The repair itself still succeeds — the lesson write is best-effort.
    assert report.status == "repaired"
    # remember() ran before the failing episodic save.
    assert len(agent.remembered) == 1
    # The failure is observable, not silent: no success log, an explicit
    # failure event carrying the error type.
    assert not any(e == "repair_lesson_saved" for e, _ in agent.log.events)
    failures = [p for e, p in agent.log.events if e == "repair_lesson_save_failed"]
    assert len(failures) == 1
    assert failures[0]["error_type"] == "RuntimeError"
    assert failures[0]["path"] == "core/widget.py"


# --------------------------------------------------------------------------- #
# trace_id diagnostic-logs path                                                 #
# --------------------------------------------------------------------------- #

def test_trace_id_runs_diagnostic_logs_step_before_baseline(tmp_path: Path):
    outputs = _success_tool_outputs()
    outputs["read_logs"] = {"output": {"events": [], "count": 0}}
    agent = _FakeAgent(tool_outputs=outputs, with_memory=True)
    controller = SelfRepairController(agent, workspace_root=tmp_path)

    report = controller.run(_proposal(trace_id="trace-abc"))

    assert report.status == "repaired"
    # The diagnostic_logs step ran first and succeeded.
    diag = [s for s in report.steps if s.name == "diagnostic_logs"]
    assert diag and diag[0].status == "ok"
    assert diag[0].tool == "read_logs"
    assert report.steps[0].name == "diagnostic_logs"


# --------------------------------------------------------------------------- #
# rollback itself vetoed by governance                                          #
# --------------------------------------------------------------------------- #

class _DenyRollbackGovernance(GovernancePolicy):
    """Allows the write but vetoes the compensating rollback."""

    def evaluate(self, *, mode, operation, **kw) -> GovernanceDecision:  # type: ignore[override]
        op = operation if isinstance(operation, GovernedOperation) else GovernedOperation(operation)
        if op is GovernedOperation.ROLLBACK:
            return GovernanceDecision(
                mode=AgentMode.REPAIR,
                operation=op,
                verdict="deny",
                reasons=("governance vetoes rollback",),
            )
        return super().evaluate(mode=mode, operation=operation, **kw)


def test_governance_denied_rollback_marks_report_failed(tmp_path: Path):
    # baseline green (5 passed) then a regressing patch (post: 2 passed, 3
    # failed) → measured_confidence 0.4 < 0.6 → controller tries to roll back,
    # but governance vetoes ROLLBACK → status failed, rollback recorded as
    # governance_denied, agent.rollback() never invoked.
    outputs = _success_tool_outputs()
    outputs["run_tests"] = {
        "outputs": [
            {"output": {"timed_out": False, "exit_code": 0, "passed": 5, "failed": 0, "errors": 0}},
            {"output": {"timed_out": False, "exit_code": 1, "passed": 2, "failed": 3, "errors": 0}},
        ]
    }
    agent = _FakeAgent(tool_outputs=outputs)
    controller = SelfRepairController(
        agent, workspace_root=tmp_path, governance_policy=_DenyRollbackGovernance()
    )

    report = controller.run(_proposal())

    assert report.status == "failed"
    # The write DID happen (compensation registered) but rollback was vetoed.
    assert len(agent.compensation_log) == 1
    assert agent.rolled_back_plan_ids == []  # agent.rollback() never called
    rb = [s for s in report.steps if s.name == "rollback"]
    assert rb and rb[0].status == "governance_denied"


# --------------------------------------------------------------------------- #
# write physically applied then failed the controller's own validation gate    #
# --------------------------------------------------------------------------- #

def test_write_applied_but_invalid_output_is_rolled_back(tmp_path: Path):
    # The write tool_call SUCCEEDS (file changed on disk, compensation plan
    # registered inside _call_tool) but the controller's post-call output
    # validation rejects it → write.ok is False. The physical change must NOT
    # be left orphaned: the controller has to roll back the registered plan
    # before returning, not bail out silently.
    outputs = _success_tool_outputs()
    # status=success → _call_tool registers the compensation plan; valid=False
    # → validate_output fails afterwards → the write step reports verify_failed.
    outputs["file_write"] = {
        "output": {"path": "core/example.py", "bytes_written": 16},
        "valid": False,
    }
    agent = _FakeAgent(tool_outputs=outputs)
    controller = SelfRepairController(agent, workspace_root=tmp_path)

    report = controller.run(_proposal())

    # A compensation plan was registered by the (physically applied) write...
    assert len(agent.compensation_log) == 1
    registered_plan_id = agent.compensation_log[0].id
    # ...and the controller rolled it back instead of leaving an orphaned change.
    assert agent.rolled_back_plan_ids == [registered_plan_id]
    assert report.compensation_plan_id == registered_plan_id
    # The failed write is still visible as its own step, and a rollback step
    # was appended with a clean (error-free) result.
    write_steps = [s for s in report.steps if s.name == "write"]
    assert write_steps and write_steps[0].status == "verify_failed"
    rb = [s for s in report.steps if s.name == "rollback"]
    assert rb and rb[0].status == "ok"
    assert report.status == "rolled_back"

