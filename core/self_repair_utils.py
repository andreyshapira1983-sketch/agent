from __future__ import annotations

from typing import Any


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


def _blocked_status(status: str):
    if status == "approval_deny":
        return "approval_denied"
    if status == "approval_abort":
        return "approval_aborted"
    if status == "approval_unavailable":
        return "approval_unavailable"
    if status in {"tool_error", "verify_failed"}:
        return "failed"
    return "blocked"


def _first_output(steps: list[Any], name: str) -> Any:
    for step in steps:
        if step.name == name:
            return step.output
    return None


def _approval_summary(steps: list[Any]) -> str | None:
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
