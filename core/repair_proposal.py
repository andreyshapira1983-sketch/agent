"""MVP-13.3 repair proposal generation.

This layer gives the agent a way to *think up* a repair, while keeping the
existing SelfRepairController as the only layer that may apply it.

Flow:

    run_tests + optional read_logs + target file
        -> LLM strict JSON
        -> validate target/content/evidence/confidence
        -> diff preview
        -> RepairProposal

No file is written here. Bad JSON, wrong target files, giant diffs, secrets,
empty patches, and passing baseline tests all stop before a proposal can reach
the repair controller.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.llm import LLM
from core.redaction import redact_payload, redact_text
from core.secret_scanner import contains_secret
from core.self_repair import RepairProposal
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool


ProposalStatus = Literal[
    "proposed",
    "no_failing_tests",
    "rejected",
    "llm_error",
    "tool_error",
]

DEFAULT_MAX_CONTEXT_CHARS = 16_000
DEFAULT_MAX_CHANGED_LINES = 200


@dataclass
class ProposalGenerationReport:
    status: ProposalStatus
    proposal: RepairProposal | None = None
    diagnosis: str = ""
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)
    raw_response: str = ""
    baseline_tests: dict[str, Any] | None = None
    diagnostic_logs: dict[str, Any] | None = None
    diff_preview: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "proposed" and self.proposal is not None

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dry_run": True,
            "proposal": self.proposal.to_log_payload() if self.proposal else None,
            "diagnosis": self.diagnosis,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "baseline_tests": _test_summary(self.baseline_tests),
            "diagnostic_logs": _log_summary(self.diagnostic_logs),
            "diff_preview": _diff_summary(self.diff_preview),
        }

    def user_summary(self) -> str:
        """Compact human-facing proposal report. It never implies a write."""
        s = self.summary()
        lines = [
            f"repair proposal status={s['status']} dry_run=True",
            f"confidence={s['confidence']:.2f}",
        ]
        proposal = s.get("proposal") or {}
        if proposal.get("path"):
            lines.append(f"target={proposal['path']}")
        if s.get("diagnosis"):
            lines.append(f"diagnosis={s['diagnosis']}")
        baseline = s.get("baseline_tests")
        if baseline:
            lines.append(
                "baseline_tests="
                f"exit={baseline.get('exit_code')} passed={baseline.get('passed')} "
                f"failed={baseline.get('failed')} errors={baseline.get('errors')}"
            )
        diff = s.get("diff_preview")
        if diff:
            lines.append(
                f"diff=+{diff.get('additions')} -{diff.get('deletions')} "
                f"truncated={diff.get('diff_truncated')}"
            )
        for warning in s.get("warnings") or []:
            lines.append(f"warning={warning}")
        return "\n".join(lines)


class RepairProposalGenerator:
    """Generate a validated RepairProposal for one target file."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        llm: LLM,
        logger: Any | None = None,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_changed_lines: int = DEFAULT_MAX_CHANGED_LINES,
    ):
        if not Path(workspace_root).is_dir():
            raise ValueError(f"workspace_root must be a directory: {workspace_root}")
        if max_context_chars <= 0:
            raise ValueError("max_context_chars must be > 0")
        if max_changed_lines <= 0:
            raise ValueError("max_changed_lines must be > 0")
        self.workspace_root = Path(workspace_root).resolve()
        self.llm = llm
        self.log = logger
        self.max_context_chars = int(max_context_chars)
        self.max_changed_lines = int(max_changed_lines)
        self.file_read = FileReadTool(self.workspace_root)
        self.run_tests = RunTestsTool(self.workspace_root)
        self.read_logs = ReadLogsTool(self.workspace_root)
        self.diff_file = DiffFileTool(self.workspace_root)

    def generate(
        self,
        *,
        target_path: str,
        test_paths: tuple[str, ...] = ("tests",),
        test_pattern: str | None = None,
        trace_id: str | None = None,
        extra_context: str = "",
    ) -> ProposalGenerationReport:
        self._log("repair_proposal_start", {
            "target_path": target_path,
            "test_paths": list(test_paths),
            "test_pattern": test_pattern,
            "trace_id": trace_id,
        })

        warnings: list[str] = []
        baseline = self._run_tests(test_paths=test_paths, test_pattern=test_pattern)
        if not baseline["ok"]:
            return self._finish(ProposalGenerationReport(
                status="tool_error",
                warnings=[baseline["error"]],
                baseline_tests=baseline.get("output"),
            ))

        baseline_output = baseline["output"]
        if _tests_passed(baseline_output):
            return self._finish(ProposalGenerationReport(
                status="no_failing_tests",
                warnings=["baseline tests are already green; refusing to invent a repair"],
                baseline_tests=baseline_output,
            ))

        logs_output: dict[str, Any] | None = None
        if trace_id:
            logs = self._read_logs(trace_id=trace_id)
            if logs["ok"]:
                logs_output = logs["output"]
            else:
                warnings.append(logs["error"])

        try:
            current_content = self.file_read.run(target_path)
        except Exception as exc:
            return self._finish(ProposalGenerationReport(
                status="tool_error",
                warnings=[f"file_read failed: {type(exc).__name__}: {exc}"],
                baseline_tests=baseline_output,
                diagnostic_logs=logs_output,
            ))

        current_safe, findings = redact_text(current_content)
        if findings:
            warnings.append("target file content was redacted before LLM prompt")

        prompt = self._build_prompt(
            target_path=target_path,
            current_content=current_safe,
            baseline_tests=baseline_output,
            diagnostic_logs=logs_output,
            extra_context=extra_context,
            test_paths=test_paths,
            test_pattern=test_pattern,
        )
        raw = self.llm.complete(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=4096,
            temperature=0.0,
        )
        parsed = _parse_json_object(raw)
        if not parsed["ok"]:
            return self._finish(ProposalGenerationReport(
                status="llm_error",
                warnings=[parsed["error"]],
                raw_response=raw,
                baseline_tests=baseline_output,
                diagnostic_logs=logs_output,
            ))

        report = self._validate_draft(
            draft=parsed["data"],
            raw_response=raw,
            requested_target=target_path,
            test_paths=test_paths,
            test_pattern=test_pattern,
            trace_id=trace_id,
            baseline_tests=baseline_output,
            diagnostic_logs=logs_output,
            warnings=warnings,
        )
        return self._finish(report)

    def _validate_draft(
        self,
        *,
        draft: dict[str, Any],
        raw_response: str,
        requested_target: str,
        test_paths: tuple[str, ...],
        test_pattern: str | None,
        trace_id: str | None,
        baseline_tests: dict[str, Any],
        diagnostic_logs: dict[str, Any] | None,
        warnings: list[str],
    ) -> ProposalGenerationReport:
        reasons = list(warnings)
        target = draft.get("target_file")
        if not isinstance(target, str) or not target.strip():
            reasons.append("target_file must be a non-empty string")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        target = target.strip()
        if target != requested_target:
            reasons.append(
                f"target_file {target!r} does not match requested target {requested_target!r}"
            )
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        if not _is_safe_relative_ascii_path(target):
            reasons.append("target_file must be a safe relative ASCII path")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        if not (self.workspace_root / target).resolve().is_file():
            reasons.append("target_file does not exist")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)

        proposed = draft.get("proposed_content")
        if not isinstance(proposed, str) or not proposed.strip():
            reasons.append("proposed_content must be a non-empty string")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        has_secret, secret_reasons = contains_secret(proposed)
        if has_secret:
            reasons.append("proposed_content contains secret material: " + "; ".join(secret_reasons))
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)

        confidence = _coerce_confidence(draft.get("confidence"))
        if confidence is None:
            reasons.append("confidence must be a number in [0, 1]")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)

        diagnosis = draft.get("diagnosis")
        if not isinstance(diagnosis, str):
            diagnosis = ""
        diagnosis = diagnosis.strip()

        evidence = _coerce_evidence(draft.get("evidence"))
        if not evidence:
            reasons.append("evidence must contain at least one string")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)

        try:
            diff = self.diff_file.run(
                path=target,
                proposed_content=proposed,
                context_lines=3,
            )
        except Exception as exc:
            reasons.append(f"diff_file rejected proposal: {type(exc).__name__}: {exc}")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)

        ok, issues = self.diff_file.validate_output(diff)
        if not ok:
            reasons.append("diff preview invalid: " + "; ".join(issues))
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        changed = int(diff.get("additions") or 0) + int(diff.get("deletions") or 0)
        if changed == 0:
            reasons.append("proposal produces an empty diff")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        if changed > self.max_changed_lines:
            reasons.append(
                f"proposal changes too many lines ({changed} > {self.max_changed_lines})"
            )
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)
        if diff.get("diff_truncated"):
            reasons.append("diff preview was truncated")
            return self._rejected(raw_response, baseline_tests, diagnostic_logs, reasons)

        proposal = RepairProposal(
            path=target,
            proposed_content=proposed,
            test_paths=test_paths,
            test_pattern=test_pattern,
            trace_id=trace_id,
            reason=diagnosis or "generated repair proposal",
            context_lines=3,
            confidence=confidence,
            evidence=evidence,
        )
        return ProposalGenerationReport(
            status="proposed",
            proposal=proposal,
            diagnosis=diagnosis,
            confidence=confidence,
            evidence=evidence,
            warnings=reasons,
            raw_response=raw_response,
            baseline_tests=baseline_tests,
            diagnostic_logs=diagnostic_logs,
            diff_preview=redact_payload(diff),
        )

    def _rejected(
        self,
        raw_response: str,
        baseline_tests: dict[str, Any],
        diagnostic_logs: dict[str, Any] | None,
        warnings: list[str],
    ) -> ProposalGenerationReport:
        return ProposalGenerationReport(
            status="rejected",
            warnings=warnings,
            raw_response=raw_response,
            baseline_tests=baseline_tests,
            diagnostic_logs=diagnostic_logs,
        )

    def _run_tests(
        self,
        *,
        test_paths: tuple[str, ...],
        test_pattern: str | None,
    ) -> dict[str, Any]:
        try:
            output = self.run_tests.run(paths=list(test_paths), pattern=test_pattern)
            ok, issues = self.run_tests.validate_output(output)
            if not ok:
                return {"ok": False, "error": "run_tests invalid output: " + "; ".join(issues), "output": output}
            return {"ok": True, "output": redact_payload(output)}
        except Exception as exc:
            return {"ok": False, "error": f"run_tests failed: {type(exc).__name__}: {exc}"}

    def _read_logs(self, *, trace_id: str) -> dict[str, Any]:
        try:
            output = self.read_logs.run(
                trace_id=trace_id,
                last_n=100,
                event_filter=["error", "verify", "replan", "replan_exhausted"],
            )
            ok, issues = self.read_logs.validate_output(output)
            if not ok:
                return {"ok": False, "error": "read_logs invalid output: " + "; ".join(issues), "output": output}
            return {"ok": True, "output": redact_payload(output)}
        except Exception as exc:
            return {"ok": False, "error": f"read_logs failed: {type(exc).__name__}: {exc}"}

    def _build_prompt(
        self,
        *,
        target_path: str,
        current_content: str,
        baseline_tests: dict[str, Any],
        diagnostic_logs: dict[str, Any] | None,
        extra_context: str,
        test_paths: tuple[str, ...],
        test_pattern: str | None,
    ) -> str:
        code = _truncate(current_content, self.max_context_chars)
        payload = {
            "target_path": target_path,
            "test_paths": list(test_paths),
            "test_pattern": test_pattern,
            "baseline_tests": _test_summary(baseline_tests),
            "failed_tests": baseline_tests.get("failed_tests") or [],
            "diagnostic_logs": _log_summary(diagnostic_logs),
            "extra_context": _truncate(extra_context, 2000),
        }
        return (
            "Repair target and diagnostics:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n\n"
            f"Current content of {target_path}:\n"
            "```text\n"
            f"{code}\n"
            "```\n\n"
            "Return only strict JSON with keys: diagnosis, target_file, "
            "proposed_content, evidence, confidence."
        )

    def _finish(self, report: ProposalGenerationReport) -> ProposalGenerationReport:
        self._log("repair_proposal_result", report.summary())
        return report

    def _log(self, event: str, payload: Any) -> None:
        if self.log is not None:
            self.log.log(event, payload)


_SYSTEM_PROMPT = """REPAIR_PROPOSAL_MODE
You generate repair proposals for a guarded autonomous agent.
Return ONLY valid JSON, no markdown.
Schema:
{
  "diagnosis": "short explanation grounded in tests/logs/code",
  "target_file": "same relative ASCII path as requested",
  "proposed_content": "complete replacement content for the target file",
  "evidence": ["failed test name or log/code fact", "..."],
  "confidence": 0.0
}
Rules:
- Do not change target_file.
- Do not include secrets or credentials.
- Prefer minimal changes.
- If unsure, still return JSON, but lower confidence.
"""

# §3.x — register this prompt with the global Prompt Registry
try:
    from core.prompt_registry import register_prompt as _rp
    _rp("repair_proposal.system", _SYSTEM_PROMPT, module="core.repair_proposal",
        description="System prompt for the LLM repair proposal generator")
except ImportError:  # pragma: no cover
    pass


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"LLM returned invalid JSON: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "LLM JSON must be an object"}
    return {"ok": True, "data": data}


def _tests_passed(output: dict[str, Any] | None) -> bool:
    if not isinstance(output, dict):
        return False
    return (
        output.get("timed_out") is False
        and output.get("exit_code") == 0
        and int(output.get("failed") or 0) == 0
        and int(output.get("errors") or 0) == 0
    )


def _coerce_confidence(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out < 0.0 or out > 1.0:
        return None
    return out


def _coerce_evidence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:500])
    return tuple(out)


def _is_safe_relative_ascii_path(value: str) -> bool:
    if not value or not value.isascii():
        return False
    p = Path(value)
    return not p.is_absolute() and ".." not in p.parts


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]\n"


def _test_summary(output: dict[str, Any] | None) -> dict[str, Any] | None:
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


def _log_summary(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    return {
        "trace_id": output.get("trace_id"),
        "events_returned": output.get("events_returned"),
        "total_events": output.get("total_events"),
        "filtered": output.get("filtered"),
    }


def _diff_summary(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    return {
        "path": output.get("path"),
        "file_exists": output.get("file_exists"),
        "additions": output.get("additions"),
        "deletions": output.get("deletions"),
        "diff_truncated": output.get("diff_truncated"),
    }
