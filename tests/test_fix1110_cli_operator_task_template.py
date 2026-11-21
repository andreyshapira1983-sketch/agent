"""The programming `:operator-task` report says its plan is a fixed template."""
from __future__ import annotations

from app.operator_task import (
    _format_operator_programming_task_report,
    _operator_patch_diff_plan,
)

_FILES = [{"path": "billing/invoice.py", "source_id": "file:billing/invoice.py", "bytes": 1, "lines": 1}]


def _payload(task: str) -> dict:
    return {
        "goal": "patch_proposal",
        "mode": "source_review",
        "constraints": [],
        "evidence": {"files": _FILES},
        "minimal_diff_plan": _operator_patch_diff_plan(_FILES, task),
    }


def test_report_labels_the_plan_as_a_fixed_template():
    report = _format_operator_programming_task_report(_payload("fix the rounding bug"))
    assert "fixed checklist template" in report
    assert "not derived from the task" in report
    assert report.index("fixed checklist template") < report.index("1. functions/files")
