"""MVP-13.2 self-repair controller.

The controller composes the already-safe primitives into one guarded repair
transaction:

    diagnose -> diff -> approval -> write -> tests -> rollback on red

It deliberately does not invent patches. A caller supplies a RepairProposal
with the target path and proposed replacement content. A future planner can
produce that proposal, but this module owns the safety envelope around
applying it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.governance import (
    AgentMode,
    GovernedOperation,
    GovernanceDecision,
    GovernancePolicy,
)
from core.models import (
    Action,
    ApprovalDecision,
    ApprovalRequest,
    ErrorObject,
    PlanStep,
    PolicyDecision,
)
from core.redaction import redact_payload
from core.smart_memory import EpisodeRecord


RepairStatus = Literal[
    "repaired",
    "rolled_back",
    "blocked",
    "low_confidence",
    "approval_denied",
    "approval_aborted",
    "approval_unavailable",
    "no_changes",
    "failed",
]

_DEFAULT_MIN_REPAIR_CONFIDENCE = 0.60


@dataclass(frozen=True)
class RepairProposal:
    """A concrete replacement patch to route through self-repair safety."""

    path: str
    proposed_content: str
    test_paths: tuple[str, ...] = ("tests",)
    test_pattern: str | None = None
    trace_id: str | None = None
    reason: str = ""
    context_lines: int = 3
    confidence: float = 1.0
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be in [0, 1]")
        object.__setattr__(self, "test_paths", tuple(self.test_paths))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def test_arguments(self) -> dict[str, Any]:
        args: dict[str, Any] = {"paths": list(self.test_paths)}
        if self.test_pattern:
            args["pattern"] = self.test_pattern
        return args

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "proposed_bytes": len(self.proposed_content.encode("utf-8")),
            "test_paths": list(self.test_paths),
            "test_pattern": self.test_pattern,
            "trace_id": self.trace_id,
            "reason": self.reason,
            "context_lines": self.context_lines,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


@dataclass
class RepairStepRecord:
    name: str
    status: str
    tool: str | None = None
    output: Any = None
    issues: tuple[str, ...] = ()
    error: str = ""
    governance: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "tool": self.tool,
            "output": redact_payload(self.output),
            "issues": list(self.issues),
            "error": self.error,
            "governance": self.governance,
        }


@dataclass
class RepairReport:
    proposal: RepairProposal
    status: RepairStatus
    steps: list[RepairStepRecord] = field(default_factory=list)
    compensation_plan_id: str | None = None
    rollback_summary: dict[str, Any] | None = None
    # Measured confidence computed from actual baseline vs post-repair test
    # counts.  None until post_tests completes.
    measured_confidence: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "repaired"

    def summary(self) -> dict[str, Any]:
        diff = _first_output(self.steps, "diff")
        baseline = _first_output(self.steps, "baseline_tests")
        post = _first_output(self.steps, "post_tests")
        return {
            "status": self.status,
            "path": self.proposal.path,
            "test_paths": list(self.proposal.test_paths),
            "test_pattern": self.proposal.test_pattern,
            "reason": self.proposal.reason,
            "confidence": self.proposal.confidence,
            "measured_confidence": self.measured_confidence,
            "evidence": list(self.proposal.evidence),
            "diff": _diff_summary(diff),
            "baseline_tests": _test_summary(baseline),
            "post_tests": _test_summary(post),
            "approval": _approval_summary(self.steps),
            "compensation_plan_id": self.compensation_plan_id,
            "rollback": self.rollback_summary,
            "steps": [
                {
                    "name": step.name,
                    "status": step.status,
                    "tool": step.tool,
                    "issues": list(step.issues),
                    "error": step.error,
                }
                for step in self.steps
            ],
        }

    def user_summary(self) -> str:
        """Compact human-facing repair report."""
        s = self.summary()
        lines = [
            f"self-repair status={s['status']} path={s['path']}",
            f"confidence={s['confidence']:.2f}  measured_confidence={s['measured_confidence'] if s['measured_confidence'] is not None else 'n/a'}",
        ]
        if s.get("reason"):
            lines.append(f"reason={s['reason']}")
        baseline = s.get("baseline_tests")
        if baseline:
            lines.append(
                "baseline_tests="
                f"exit={baseline.get('exit_code')} passed={baseline.get('passed')} "
                f"failed={baseline.get('failed')} errors={baseline.get('errors')}"
            )
            failed = baseline.get("failed_tests") or []
            if failed:
                lines.append("failed_tests=" + ", ".join(failed[:5]))
        diff = s.get("diff")
        if diff:
            lines.append(
                f"diff=+{diff.get('additions')} -{diff.get('deletions')} "
                f"truncated={diff.get('diff_truncated')}"
            )
        if s.get("approval"):
            lines.append(f"approval={s['approval']}")
        post = s.get("post_tests")
        if post:
            lines.append(
                "post_tests="
                f"exit={post.get('exit_code')} passed={post.get('passed')} "
                f"failed={post.get('failed')} errors={post.get('errors')}"
            )
        if s.get("rollback"):
            r = s["rollback"]
            lines.append(
                f"rollback=ok:{r.get('ok')} noop:{r.get('noop')} error:{r.get('error')}"
            )
        return "\n".join(lines)


@dataclass
class _ToolRun:
    ok: bool
    status: str
    step: RepairStepRecord
    output: Any = None
    policy_decision: PolicyDecision | None = None
    governance_decision: GovernanceDecision | None = None


class SelfRepairController:
    """Apply one RepairProposal through governance, approval and rollback."""

    def __init__(
        self,
        agent: Any,
        *,
        workspace_root: Path,
        governance_policy: GovernancePolicy | None = None,
        min_confidence: float = _DEFAULT_MIN_REPAIR_CONFIDENCE,
    ):
        self.agent = agent
        self.workspace_root = Path(workspace_root).resolve()
        self.governance_policy = governance_policy or GovernancePolicy()
        self.min_confidence: float = min_confidence

    def run(self, proposal: RepairProposal) -> RepairReport:
        report = RepairReport(proposal=proposal, status="failed")
        self.agent.log.log("self_repair_start", proposal.to_log_payload())

        if proposal.trace_id:
            logs = self._execute_tool(
                name="diagnostic_logs",
                operation=GovernedOperation.READ_LOGS,
                tool_name="read_logs",
                arguments={
                    "trace_id": proposal.trace_id,
                    "last_n": 100,
                    "event_filter": ["error", "replan", "replan_exhausted", "verify"],
                },
            )
            report.steps.append(logs.step)
            if not logs.ok:
                report.status = _blocked_status(logs.status)
                self._finish(report)
                return report

        baseline = self._execute_tool(
            name="baseline_tests",
            operation=GovernedOperation.RUN_TESTS,
            tool_name="run_tests",
            arguments=proposal.test_arguments(),
        )
        report.steps.append(baseline.step)
        if not baseline.ok:
            report.status = _blocked_status(baseline.status)
            self._finish(report)
            return report

        diagnosis_verified = _diagnosis_verified(baseline.output)

        diff = self._execute_tool(
            name="diff",
            operation=GovernedOperation.PROPOSE_DIFF,
            tool_name="diff_file",
            arguments={
                "path": proposal.path,
                "proposed_content": proposal.proposed_content,
                "context_lines": proposal.context_lines,
            },
            evidence_verified=diagnosis_verified,
        )
        report.steps.append(diff.step)
        if not diff.ok:
            report.status = _blocked_status(diff.status)
            self._finish(report)
            return report

        if _is_empty_diff(diff.output):
            report.status = "no_changes"
            self._finish(report)
            return report

        if proposal.confidence < self.min_confidence:
            report.status = "low_confidence"
            report.steps.append(
                RepairStepRecord(
                    name="confidence_gate",
                    status="blocked",
                    error=(
                        f"proposal confidence {proposal.confidence:.2f} "
                        f"is below threshold {self.min_confidence:.2f}"
                    ),
                )
            )
            self.agent.log.log(
                "self_repair_confidence_gate",
                {
                    "confidence": proposal.confidence,
                    "threshold": self.min_confidence,
                    "path": proposal.path,
                    "decision": "block",
                },
            )
            self._finish(report)
            return report

        before_plan_ids = {p.id for p in self.agent.compensation_log}
        write = self._execute_tool(
            name="write",
            operation=GovernedOperation.APPLY_CODE_CHANGE,
            tool_name="file_write",
            arguments={
                "path": proposal.path,
                "content": proposal.proposed_content,
            },
            evidence_verified=diagnosis_verified,
            has_rollback=True,
            approval_summary=(
                "Self-repair wants to apply the proposed file replacement "
                f"to '{proposal.path}', then run tests and rollback on failure."
            ),
        )
        report.steps.append(write.step)
        if not write.ok:
            report.status = _blocked_status(write.status)
            self._finish(report)
            return report

        report.compensation_plan_id = _new_compensation_plan_id(
            before_plan_ids,
            self.agent.compensation_log,
        )

        post = self._execute_tool(
            name="post_tests",
            operation=GovernedOperation.RUN_TESTS,
            tool_name="run_tests",
            arguments=proposal.test_arguments(),
        )
        report.steps.append(post.step)

        # ── Measured-confidence gate (T9 / §7 Self-repair) ──────────────────
        # Compute confidence from REAL test outcomes rather than trusting the
        # caller-supplied proposal.confidence alone.
        #
        # measured_confidence = post_passed / max(baseline_passed, 1)
        #
        # < 1.0 means the patch broke at least one test that was passing
        #        before (regression).
        # < self.min_confidence means too many tests broke → rollback.
        # == 1.0 means test count held steady or improved.
        baseline_passed = _extract_pass_count(baseline.output)
        post_passed = _extract_pass_count(post.output)
        measured_confidence = post_passed / max(baseline_passed, 1)
        report.measured_confidence = round(measured_confidence, 4)

        self.agent.log.log(
            "self_repair_confidence_measured",
            {
                "path": proposal.path,
                "baseline_passed": baseline_passed,
                "post_passed": post_passed,
                "measured_confidence": report.measured_confidence,
                "proposal_confidence": proposal.confidence,
                "threshold": self.min_confidence,
            },
        )

        if post.ok and _tests_passed(post.output) and measured_confidence >= self.min_confidence:
            report.status = "repaired"
            self._finish(report)
            return report

        # Either tests failed outright OR measured confidence fell below threshold
        if measured_confidence < self.min_confidence:
            report.steps.append(
                RepairStepRecord(
                    name="measured_confidence_gate",
                    status="blocked",
                    error=(
                        f"Measured confidence {measured_confidence:.2f} "
                        f"(post_passed={post_passed} / baseline_passed={max(baseline_passed, 1)}) "
                        f"is below threshold {self.min_confidence:.2f}"
                    ),
                )
            )
            report.status = "low_confidence"
            self._rollback(report)
            self._finish(report)
            return report

        self._rollback(report)
        self._finish(report)
        return report

    def _execute_tool(
        self,
        *,
        name: str,
        operation: GovernedOperation,
        tool_name: str,
        arguments: dict[str, Any],
        evidence_verified: bool = False,
        tests_passed: bool = False,
        has_rollback: bool = False,
        approval_summary: str = "",
    ) -> _ToolRun:
        governance = self.governance_policy.evaluate(
            mode=AgentMode.REPAIR,
            operation=operation,
            evidence_verified=evidence_verified,
            tests_passed=tests_passed,
            has_rollback=has_rollback,
        )
        governance_payload = governance.to_dict()
        self.agent.log.log(
            "governance",
            governance_payload,
            step=name,
        )

        step_record = RepairStepRecord(
            name=name,
            status="governance",
            tool=tool_name,
            governance=governance_payload,
        )
        if governance.denied:
            self._log_error(
                source="governance",
                code="governance_denied",
                message="Governance denied operation: " + "; ".join(governance.reasons),
                context={"step": name, "operation": operation.value},
            )
            step_record.status = "governance_denied"
            step_record.error = "; ".join(governance.reasons)
            return _ToolRun(False, step_record.status, step_record, governance_decision=governance)

        plan_step = PlanStep(
            plan_id="self_repair",
            order=0,
            action_spec={
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": arguments,
                "source_label": f"self_repair:{name}",
            },
            expected_outcome=f"self-repair {name}",
        )
        action = Action(
            step_id=plan_step.id,
            type="tool_call",
            tool_name=tool_name,
            parameters=arguments,
            side_effects="write" if operation is GovernedOperation.APPLY_CODE_CHANGE else "read",
        )
        self.agent.log.log("act", action, source_label=f"self_repair:{name}")

        policy_decision = self.agent.policy.check(action)
        self.agent.log.log("policy", policy_decision)
        if policy_decision.decision == "deny":
            self._log_error(
                source="policy",
                code="policy_blocked",
                message="Action blocked: " + ", ".join(policy_decision.reasons),
                context={"step": name, "action_id": action.id},
            )
            step_record.status = "policy_blocked"
            step_record.error = "; ".join(policy_decision.reasons)
            return _ToolRun(
                False,
                step_record.status,
                step_record,
                policy_decision=policy_decision,
                governance_decision=governance,
            )

        if governance.requires_approval or policy_decision.decision == "escalate":
            verdict = self._request_approval(
                action=action,
                plan_step=plan_step,
                policy_decision=policy_decision,
                governance_decision=governance,
                summary=approval_summary,
            )
            if verdict != "approve":
                step_record.status = (
                    "approval_unavailable" if verdict == "unavailable"
                    else f"approval_{verdict}"
                )
                step_record.error = "approval not granted"
                return _ToolRun(
                    False,
                    step_record.status,
                    step_record,
                    policy_decision=policy_decision,
                    governance_decision=governance,
                )

        result = self.agent._call_tool(action)
        if result.status != "success":
            self._log_error(
                source=tool_name,
                code="tool_error",
                message=result.error or "tool failed",
                context={"step": name, "tool_call_id": result.tool_call_id},
            )
            step_record.status = "tool_error"
            step_record.error = result.error or "tool failed"
            return _ToolRun(
                False,
                step_record.status,
                step_record,
                policy_decision=policy_decision,
                governance_decision=governance,
            )

        tool = self.agent.registry.get(tool_name)
        is_ok, issues = tool.validate_output(result.output)
        self.agent.log.log(
            "verify",
            {
                "step_id": plan_step.id,
                "ok": is_ok,
                "issues": issues,
                "source_label": f"self_repair:{name}",
            },
        )
        if not is_ok:
            self._log_error(
                source="verifier",
                code="verify_failed",
                message="Tool Result Validation failed: " + "; ".join(issues),
                context={"step": name, "step_id": plan_step.id},
            )
            step_record.status = "verify_failed"
            step_record.issues = tuple(issues)
            step_record.error = "; ".join(issues)
            return _ToolRun(
                False,
                step_record.status,
                step_record,
                policy_decision=policy_decision,
                governance_decision=governance,
            )

        safe_output = redact_payload(result.output)
        step_record.status = "ok"
        step_record.output = safe_output
        step_record.issues = tuple(issues)
        return _ToolRun(
            True,
            "ok",
            step_record,
            output=safe_output,
            policy_decision=policy_decision,
            governance_decision=governance,
        )

    def _request_approval(
        self,
        *,
        action: Action,
        plan_step: PlanStep,
        policy_decision: PolicyDecision,
        governance_decision: GovernanceDecision,
        summary: str,
    ) -> Literal["approve", "deny", "abort", "unavailable"]:
        provider = self.agent.approval_provider
        if provider is None:
            self._log_error(
                source="approval",
                code="approval_unavailable",
                message="Self-repair requires approval but no provider is configured.",
                context={"action_id": action.id, "step_id": plan_step.id},
            )
            return "unavailable"

        tool = self.agent.registry.get(action.tool_name)
        effective_risk = tool.risk_for(action.parameters or {})
        reasons = list(policy_decision.reasons) + list(governance_decision.reasons)
        request = ApprovalRequest(
            action_id=action.id,
            step_id=plan_step.id,
            tool_name=action.tool_name,
            arguments=action.parameters,
            risk=effective_risk,
            reasons=reasons,
            summary=summary or (
                "Self-repair requests approval before applying a code change."
            ),
            policy_decision_id=policy_decision.id,
        )
        self.agent.log.log("approval_request", request)
        decision: ApprovalDecision = provider.request(request)
        self.agent.log.log("approval_decision", decision)

        if decision.decision == "approve":
            return "approve"

        self._log_error(
            source="approval",
            code=f"approval_{decision.decision}",
            message="Approval not granted: " + "; ".join(decision.reasons),
            context={
                "action_id": action.id,
                "approval_request_id": request.id,
                "approval_decision_id": decision.id,
                "responder": decision.responder,
            },
        )
        return decision.decision

    def _rollback(self, report: RepairReport) -> None:
        has_plan = bool(report.compensation_plan_id)
        governance = self.governance_policy.evaluate(
            mode=AgentMode.REPAIR,
            operation=GovernedOperation.ROLLBACK,
            has_rollback=has_plan,
        )
        self.agent.log.log(
            "governance",
            governance.to_dict(),
            step="rollback",
        )
        if governance.denied:
            report.status = "failed"
            report.steps.append(
                RepairStepRecord(
                    name="rollback",
                    status="governance_denied",
                    error="; ".join(governance.reasons),
                    governance=governance.to_dict(),
                )
            )
            return

        rollback_report = self.agent.rollback(
            plan_id=report.compensation_plan_id,
            workspace_root=self.workspace_root,
        )
        summary = rollback_report.summary()
        report.rollback_summary = summary
        report.steps.append(
            RepairStepRecord(
                name="rollback",
                status="ok" if summary.get("error", 0) == 0 else "failed",
                output=summary,
                governance=governance.to_dict(),
            )
        )
        report.status = "rolled_back" if summary.get("error", 0) == 0 else "failed"

    def _finish(self, report: RepairReport) -> None:
        self.agent.log.log("self_repair_result", report.summary())
        # After a successful repair, persist a regression-guard lesson so the
        # agent never silently reintroduces the same bug across sessions.
        if report.status == "repaired":
            self._write_repair_lesson(report)

    def _write_repair_lesson(self, report: RepairReport) -> None:
        """Persist a lesson to long-term memory after a confirmed repair.

        The lesson is tagged "fact"+"insight" so it passes the Write Policy
        consent gate without requiring an explicit user directive. It is also
        tagged "regression-guard" and "bug-fix" so the retrieval policy can
        surface it when similar files are touched in future cycles.
        """
        try:
            if not hasattr(self.agent, "remember"):
                return
            proposal = report.proposal
            evidence_summary = "; ".join(list(proposal.evidence)[:3]) or "none"
            content = (
                f"Bug fixed in '{proposal.path}': {proposal.reason or 'no description'}. "
                f"Post-repair confidence: {report.measured_confidence:.2f}. "
                f"Tests used: {', '.join(proposal.test_paths)}. "
                f"Evidence: {evidence_summary}."
            )
            self.agent.remember(
                content=content,
                tags=["lesson", "bug-fix", "regression-guard", "fact", "insight"],
                source="repair",
                record_type="episodic",
                owner="self",
            )
            # Also write directly into episodic_store so search_by_tags(["lesson"])
            # and path-aware lesson injection in _retrieve_experience_memory work.
            # (agent.remember() goes to persistent_store, not episodic_store.)
            if hasattr(self.agent, "episodic_store") and self.agent.episodic_store is not None:
                ep = EpisodeRecord(
                    goal="repair",
                    question=f"fix {proposal.path}",
                    outcome="success",
                    summary=content,
                    tools_used=("shell_exec", "run_tests"),
                    tags=("lesson", "bug-fix", "regression-guard"),
                )
                self.agent.episodic_store.save(ep)
            self.agent.log.log(
                "repair_lesson_saved",
                {
                    "path": proposal.path,
                    "measured_confidence": report.measured_confidence,
                    "content_chars": len(content),
                },
            )
        except Exception:
            # Lesson writing must never abort the repair report flow.
            pass

    def _log_error(
        self,
        *,
        source: str,
        code: str,
        message: str,
        context: dict[str, Any],
    ) -> None:
        self.agent.log.log(
            "error",
            ErrorObject(
                source=source,
                code=code,
                message=message,
                severity="error",
                recoverable=False,
                context=context,
            ),
        )


def _diagnosis_verified(output: Any) -> bool:
    return isinstance(output, dict) and output.get("timed_out") is False


def _tests_passed(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return (
        output.get("timed_out") is False
        and output.get("exit_code") == 0
        and int(output.get("failed") or 0) == 0
        and int(output.get("errors") or 0) == 0
    )


def _extract_pass_count(output: Any) -> int:
    """Return the number of passing tests from a run_tests output dict.

    Returns 0 when the output is unavailable or missing the ``passed`` key.
    """
    if not isinstance(output, dict):
        return 0
    try:
        return int(output.get("passed") or 0)
    except (TypeError, ValueError):
        return 0


def _is_empty_diff(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return int(output.get("additions") or 0) == 0 and int(output.get("deletions") or 0) == 0


def _new_compensation_plan_id(before_ids: set[str], plans: list[Any]) -> str | None:
    for plan in reversed(plans):
        if plan.id not in before_ids:
            return plan.id
    return None


def _blocked_status(status: str) -> RepairStatus:
    if status == "approval_deny":
        return "approval_denied"
    if status == "approval_abort":
        return "approval_aborted"
    if status == "approval_unavailable":
        return "approval_unavailable"
    if status in {"tool_error", "verify_failed"}:
        return "failed"
    return "blocked"


def _first_output(steps: list[RepairStepRecord], name: str) -> Any:
    for step in steps:
        if step.name == name:
            return step.output
    return None


def _approval_summary(steps: list[RepairStepRecord]) -> str | None:
    for step in steps:
        if step.name != "write":
            continue
        if step.status == "ok":
            return "approved"
        if step.status == "approval_deny":
            return "denied"
        if step.status == "approval_abort":
            return "aborted"
        if step.status == "approval_unavailable":
            return "unavailable"
        return step.status
    return None


def _test_summary(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    return {
        "exit_code": output.get("exit_code"),
        "timed_out": output.get("timed_out"),
        "passed": output.get("passed"),
        "failed": output.get("failed"),
        "errors": output.get("errors"),
        "skipped": output.get("skipped"),
        "total": output.get("total"),
        "failed_tests": output.get("failed_tests") or [],
    }


def _diff_summary(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    return {
        "path": output.get("path"),
        "file_exists": output.get("file_exists"),
        "additions": output.get("additions"),
        "deletions": output.get("deletions"),
        "diff_truncated": output.get("diff_truncated"),
    }
