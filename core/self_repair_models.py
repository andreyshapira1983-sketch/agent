from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.redaction import redact_payload

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
    measured_confidence: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "repaired"

    def summary(self) -> dict[str, Any]:
        from .self_repair_utils import _approval_summary, _diff_summary, _first_output, _test_summary

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
    policy_decision: Any | None = None
    governance_decision: Any | None = None
