"""Only pytest exit code 0 is a pass; a budget-stopped run is not `completed`."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.budget_governor import BudgetLimits
from core.circuit_breaker import CircuitBreakerConfig
from tests.test_autonomous_runtime import _agent
from tools.base import Tool


def _tests_report(workspace: Path, exit_code: int, *, passed: int = 0):
    class _RunTests(Tool):
        name = "run_tests"
        description = "fake tests with a fixed exit code"
        risk = "reversible"

        def run(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "exit_code": exit_code, "timed_out": False, "passed": passed,
                "failed": 0, "errors": 0, "failed_tests": [],
            }

    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    agent.registry.register(_RunTests())
    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(limit=3, include_tests=True)
    )
    assert report.tasks[-1].task.kind == "tests"
    return report.tasks[-1]


def test_exit_code_zero_is_a_pass(workspace: Path):
    task = _tests_report(workspace, 0, passed=3)
    assert task.status == "done"


def test_no_tests_collected_is_not_a_pass(workspace: Path):
    task = _tests_report(workspace, 5)
    assert task.status == "inconclusive"
    assert "no tests collected" in task.summary
    assert task.details["exit_code"] == 5


@pytest.mark.parametrize(
    ("exit_code", "meaning"),
    [(2, "interrupted"), (3, "internal error"), (4, "usage error")],
)
def test_broken_pytest_run_is_a_failure(workspace: Path, exit_code: int, meaning: str):
    task = _tests_report(workspace, exit_code)
    assert task.status == "failed"
    assert meaning in task.summary
    assert task.details["exit_code"] == exit_code


def test_unknown_nonzero_exit_code_is_a_failure(workspace: Path):
    task = _tests_report(workspace, 1)
    assert task.status == "failed"
    assert "exit_code=1" in task.summary


def test_budget_denial_below_the_breaker_threshold_is_not_completed(workspace: Path):
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent = _agent(workspace, with_tests=False)
    report = AutonomousRuntime(agent, workspace=workspace).run(
        AutonomousRuntimeConfig(
            limit=3, include_tests=False, budgets=BudgetLimits(max_cycles=1),
            circuit=CircuitBreakerConfig(max_budget_denials=2),
        )
    )
    assert len(report.tasks) == 1
    assert report.status == "stopped"
    assert "budget" in report.stop_reason
