"""The autonomous health check must be able to finish.

Found by running the agent, not by reading it: `:auto-run` spent five minutes on
its `tests` task and returned `inconclusive (timed_out=True)`. The task is not
broken -- it correctly refuses to read `passed=0, failed=0` as green. The budget
was: 300 s, chosen when the suite was "1800+ tests" per the comment in
`agent_tick.py`, against a suite that now collects 7262 and takes six to seven
minutes.

The intended contract is the FULL suite, and that is established from the code
rather than assumed: `agent_tick.py` says "Give the test runner enough time for a
full suite", `tools/run_tests.py` says "full suite needs ~3 min on this repo", and
`AutonomousRuntime._task_tests` invokes the tool with `paths=["tests"]`. Nothing
anywhere describes a bounded health subset, so replacing the full run with a
weaker check would be a silent contract change, not a repair.

None of these tests waits for a real timeout.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.autonomous_runtime import AutonomousRuntime, AutonomousTask
from tools.run_tests import DEFAULT_TIMEOUT_SECONDS, RunTestsTool

#: Full-suite wall time measured on this repository, warm caches, five runs:
#: 327, 333, 339, 390 and 411 seconds. Raise these numbers when the suite grows,
#: and the floor below moves with them instead of being re-guessed.
_MEASURED_SECONDS = (327.0, 333.0, 339.0, 390.0, 411.0)
_WORST_OBSERVED = max(_MEASURED_SECONDS)

#: The timeout exists to bound a HANG, not to police duration, and the costs are
#: asymmetric: too low and health can never be established at all, which is the
#: defect this file exists for; too high and a hang is merely noticed later, with
#: `timed_out -> inconclusive` still refusing to call a partial run green. So the
#: budget must clear the worst observed run by a margin wide enough for a cold
#: bytecode cache and a machine doing other work -- twice the worst measurement.
_REQUIRED_FLOOR = 2 * _WORST_OBSERVED


def test_the_default_budget_clears_the_measured_suite_duration() -> None:
    assert DEFAULT_TIMEOUT_SECONDS >= _REQUIRED_FLOOR, (
        f"the default is {DEFAULT_TIMEOUT_SECONDS}s and the suite has been measured "
        f"at up to {_WORST_OBSERVED}s. Every autonomous health check would time out."
    )


def test_the_daemon_does_not_pin_a_shorter_budget() -> None:
    """`agent_tick` sets the same variable, so raising only the tool fixes half."""
    tree = ast.parse((Path(__file__).resolve().parent.parent / "agent_tick.py")
                     .read_text(encoding="utf-8"))
    pinned = [
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setdefault"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "AGENT_TEST_TIMEOUT_SECONDS"
        and isinstance(node.args[1], ast.Constant)
    ]
    assert pinned, "agent_tick no longer pins the test timeout; re-derive this test"
    for value in pinned:
        assert float(value) >= _REQUIRED_FLOOR, (
            f"agent_tick pins {value}s, below the floor of {_REQUIRED_FLOOR}s"
        )


def test_the_environment_override_still_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TEST_TIMEOUT_SECONDS", "17")
    assert RunTestsTool(workspace_root=tmp_path).timeout_seconds == 17.0


def test_an_unset_override_falls_back_to_the_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_TEST_TIMEOUT_SECONDS", raising=False)
    assert RunTestsTool(workspace_root=tmp_path).timeout_seconds == DEFAULT_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "output,expected",
    [
        ({"timed_out": True, "exit_code": None, "passed": 0, "failed": 0}, "inconclusive"),
        ({"timed_out": False, "exit_code": None, "passed": 9, "failed": 0}, "inconclusive"),
        ({"timed_out": False, "exit_code": 0, "passed": 9, "failed": 0}, "done"),
        ({"timed_out": False, "exit_code": 1, "passed": 9, "failed": 2}, "failed"),
    ],
)
def test_a_run_that_never_finished_is_never_reported_as_health(
    output: dict, expected: str
) -> None:
    """The honest mapping is the part that must NOT change while fixing the budget."""

    class _Result:
        status = "success"
        error = None

    class _Tool:
        def invoke(self, call):
            result = _Result()
            result.output = output
            return result

    class _Registry:
        def get(self, name):
            assert name == "run_tests"
            return _Tool()

    class _Agent:
        registry = _Registry()

    class _Shim:
        agent = _Agent()

    task = AutonomousTask(kind="tests", description="health")
    report = AutonomousRuntime._task_tests(_Shim(), task)
    assert report.status == expected


def test_the_tool_marks_a_run_it_had_to_kill(tmp_path: Path) -> None:
    """A real timeout, made cheap by a tiny budget rather than a long wait."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8")
    tool = RunTestsTool(workspace_root=tmp_path, timeout_seconds=1.0)
    output = tool.run(paths=["tests"])
    assert output["timed_out"] is True
    assert output.get("exit_code") is None
