"""A broken risk probe must not look like an ordinary writing step.

Census item A7. `_step_only_reads` asks each tool whether this step, with these
arguments, can change the workspace. Three different situations produced the
same `False` and the same empty journal:

    the step genuinely writes        normal — and must stay silent
    the tool is not in the registry  a fault
    the tool's `risk_for` raised     a fault

Only the first is ordinary. The other two cost the whole batch its parallel
path — `_execute_steps_parallel` runs everything in plan order as soon as one
step is not read-only — and nothing anywhere said so. A tool whose `risk_for`
always raises would never get the parallel path again, and no journal line
would ever mention it.

Measured before the fix, and the point is the middle column:

                          only_reads   journal
    healthy read              True     (empty)
    step really writes        False    (empty)
    risk_for raised           False    (empty)   <- indistinguishable
    tool missing              False    (empty)   <- indistinguishable

What is deliberately NOT changed: the answer. Every unresolvable case still
comes back `False`, because an unknown step must not buy concurrency. Failing
safe and reporting are different jobs, and this handler only did the first.

And the normal case stays silent on purpose. An event for every effect step
would be a stream rather than a signal — the same trap A3 avoided by not
reporting an empty assumption registry as a failure.
"""
from __future__ import annotations

from typing import Any

from core.loop_step_execution import AgentLoopStepExecution
from core.models import PlanStep


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, name, payload=None, **kw):  # test spy
        self.events.append((name, payload or {}))

    def names(self) -> list[str]:
        return [n for n, _ in self.events]


class _ReadOnlyTool:
    def risk_for(self, arguments: dict) -> str:
        return "read_only"


class _WritingTool:
    def risk_for(self, arguments: dict) -> str:
        return "irreversible"


class _BrokenRiskTool:
    def risk_for(self, arguments: dict) -> str:
        raise RuntimeError("this tool cannot assess its own risk")


class _Registry:
    def __init__(self, tool: Any, *, present: bool = True) -> None:
        self.tool = tool
        self.present = present

    def get(self, name: str):
        if not self.present:
            raise KeyError(name)
        return self.tool


class _Agent(AgentLoopStepExecution):
    """Only what the probe touches — no runtime needed to ask this."""

    def __init__(self, registry: _Registry) -> None:
        self.log = _Log()
        self.registry = registry


def _step() -> PlanStep:
    return PlanStep(
        id="s1",
        plan_id="p1",
        order=1,
        description="читаем файл",
        expected_outcome="файл прочитан",
        action_spec={"tool_name": "file_read", "arguments": {"path": "a.txt"}},
    )


# ---------------------------------------------------------------------------
# The two ordinary answers stay ordinary
# ---------------------------------------------------------------------------

def test_a_healthy_read_only_step_is_recognised_and_silent():
    agent = _Agent(_Registry(_ReadOnlyTool()))

    assert agent._step_only_reads(_step()) is True
    assert agent.log.names() == []


def test_a_step_that_really_writes_is_refused_and_stays_silent():
    """The case that must NOT be reported.

    Most plans contain effect steps, and an event for each would drown the
    signal the faults below are supposed to raise.
    """
    agent = _Agent(_Registry(_WritingTool()))

    assert agent._step_only_reads(_step()) is False
    assert agent.log.names() == []


# ---------------------------------------------------------------------------
# The two faults are now distinguishable
# ---------------------------------------------------------------------------

def test_a_tool_whose_risk_probe_raises_is_reported():
    agent = _Agent(_Registry(_BrokenRiskTool()))

    assert agent._step_only_reads(_step()) is False
    assert agent.log.names() == ["step_risk_probe_failed"]

    _name, payload = agent.log.events[0]
    assert payload["reason"] == "risk_for_raised"
    assert payload["tool"] == "file_read"
    assert payload["exception_type"] == "RuntimeError"


def test_a_tool_missing_from_the_registry_is_reported():
    agent = _Agent(_Registry(_ReadOnlyTool(), present=False))

    assert agent._step_only_reads(_step()) is False
    assert agent.log.names() == ["step_risk_probe_failed"]
    assert agent.log.events[0][1]["reason"] == "unknown_tool"


def test_the_two_faults_do_not_report_the_same_reason():
    """The whole value of the fix in one assertion.

    Both cost concurrency and both used to be the same empty journal. If the
    reasons ever collapse into one string, the operator is back to guessing.
    """
    broken = _Agent(_Registry(_BrokenRiskTool()))
    missing = _Agent(_Registry(_ReadOnlyTool(), present=False))
    broken._step_only_reads(_step())
    missing._step_only_reads(_step())

    assert broken.log.events[0][1]["reason"] != missing.log.events[0][1]["reason"]


# ---------------------------------------------------------------------------
# The safe answer is unchanged
# ---------------------------------------------------------------------------

def test_reporting_did_not_change_a_single_verdict():
    """Fail-safe is the half that was already right.

    An unresolvable step must not buy concurrency, and adding a journal line
    must not have altered that for any of the four cases.
    """
    verdicts = {
        "read_only": _Agent(_Registry(_ReadOnlyTool()))._step_only_reads(_step()),
        "writes": _Agent(_Registry(_WritingTool()))._step_only_reads(_step()),
        "raises": _Agent(_Registry(_BrokenRiskTool()))._step_only_reads(_step()),
        "missing": _Agent(
            _Registry(_ReadOnlyTool(), present=False)
        )._step_only_reads(_step()),
    }

    assert verdicts == {
        "read_only": True,
        "writes": False,
        "raises": False,
        "missing": False,
    }


def test_a_failure_to_journal_does_not_break_the_probe():
    """The report is best effort; the verdict is not.

    A logger that raises must not turn a routing decision into a crashed run.
    """
    class _AngryLog(_Log):
        def log(self, name, payload=None, **kw):
            raise RuntimeError("journal is down")

    agent = _Agent(_Registry(_BrokenRiskTool()))
    agent.log = _AngryLog()

    assert agent._step_only_reads(_step()) is False
