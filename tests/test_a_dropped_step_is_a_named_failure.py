"""A step the sanitiser removes is a failure with a name, not a warning nobody reads.

Exam 2026-09-05, turns 35–36 (session exam_j): the agent planned
`findstr /s /n /c:{{step: *.py`; the sanitiser dropped the step for the `{`
and wrote why into the `planner` event's `warnings` — and nowhere else. The
plan ran without it, the synthesizer never learned a step was missing, and
asked next turn why the search had not run, the agent blamed an earlier
PermissionError and planned the same argument again.

Two things are pinned here:

  * `dropped_step_triggers` turns every `step[N]: … dropped` warning into a
    `step_dropped` ReplanTrigger carrying the warning verbatim, and that code
    is world-facing — a turn that still answered may say what it did not do.
  * braces are no longer metacharacters in `shell_exec` argv, in the sanitiser
    and in the tool alike, so the search that was dropped twice now runs.
"""
from __future__ import annotations

from pathlib import Path

from core.planner import LLMPlanner
from core.replan import (
    ReplanTrigger,
    dropped_step_triggers,
    world_facing_failures,
)
from tools.base import ToolRegistry
from tools.shell_exec import ShellExecTool


def _sanitize(workspace: Path, steps):
    reg = ToolRegistry()
    reg.register(ShellExecTool(workspace_root=workspace))

    class _StubLLM:
        def complete(self, **_kw):
            raise AssertionError("LLM must not be called in sanitiser tests")

    planner = LLMPlanner(llm=_StubLLM(), registry=reg)
    sources, warnings, _dropped = planner._validate_steps(steps, file_hint=None)
    return sources, warnings


class TestTheWarningBecomesATrigger:
    def test_a_metacharacter_drop_names_the_step_the_tool_and_the_rule(self):
        warnings = [
            (
                "step[2]: shell_exec argv[3] 'a;b' contains the shell metacharacter "
                "';' (banned: ; | & < > ` $ ( ) [ ] and newline/CR/tab/NUL), dropped"
            ),
        ]

        (trigger,) = dropped_step_triggers(warnings, attempt=1)

        assert trigger.code == "step_dropped"
        assert trigger.step_id == "step[2]"
        assert trigger.tool_name == "shell_exec"
        assert trigger.attempt == 1
        assert "not executed" in trigger.reason
        assert warnings[0] in trigger.reason, "the rule travels verbatim"


class TestTheTriggerReachesBothReaders:
    def test_a_turn_that_still_answered_may_disclose_the_drop(self):
        trigger = ReplanTrigger(
            code="step_dropped", step_id="step[1]", tool_name="shell_exec",
            arguments={}, reason="Step not executed — removed before it ran: …",
            attempt=1,
        )
        internal = ReplanTrigger(
            code="plan_parse_failed", step_id="planner", tool_name=None,
            arguments={}, reason="not json", attempt=1,
        )

        assert world_facing_failures([trigger, internal]) == [trigger]


class TestBracesAreSearchable:
    def test_a_real_metacharacter_is_still_dropped_and_the_reason_names_it(self, tmp_path: Path):
        sources, warnings = _sanitize(tmp_path, [{
            "tool": "shell_exec",
            "arguments": {"argv": ["findstr", "a;b", "*.py"]},
        }])

        assert sources == []
        assert len(warnings) == 1
        assert "'a;b'" in warnings[0]
        assert "metacharacter ';'" in warnings[0], "the offending character is named"
        assert "newline/CR/tab/NUL" in warnings[0], "the banned set is complete"
        assert warnings[0].endswith("dropped")
        assert dropped_step_triggers(warnings, attempt=1)[0].tool_name == "shell_exec"
