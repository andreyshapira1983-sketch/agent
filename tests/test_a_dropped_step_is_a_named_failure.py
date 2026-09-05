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
    ReplanPolicy,
    ReplanTrigger,
    dropped_step_triggers,
    format_replan_context,
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

    def test_every_drop_shape_is_recognised(self):
        warnings = [
            "step[0]: read_logs last_n must be an int in [1..500], got 'x', dropped",
            "step[1]: tool 'grep_all' not registered, dropped",
            "step[3]: file_read without path, dropped",
            "step[4]: memory_recall needs a non-empty term; step dropped",
            "step[5]: tool 'nope' has no sanitiser, dropped",
        ]

        triggers = dropped_step_triggers(warnings, attempt=2)

        assert [t.step_id for t in triggers] == [
            "step[0]", "step[1]", "step[3]", "step[4]", "step[5]",
        ]
        assert [t.tool_name for t in triggers] == [
            "read_logs", "grep_all", "file_read", "memory_recall", "nope",
        ]

    def test_a_clamp_or_a_trimmed_argument_is_not_a_drop(self):
        warnings = [
            "step[0]: read_logs last_n 1000 outside [1..500], clamped to 500",
            "step[1]: web_search max_results not an int ('9'), defaulting to 5",
            "step[2]: memory_recall dropping unexpected args ['x']",
            "plan_parse_failed",
        ]

        assert dropped_step_triggers(warnings, attempt=1) == []

    def test_nothing_in_nothing_out(self):
        assert dropped_step_triggers(None, attempt=1) == []
        assert dropped_step_triggers([], attempt=1) == []


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

    def test_a_replan_carries_the_rule_to_the_planner(self):
        trigger = dropped_step_triggers(
            [(
                "step[1]: shell_exec argv[2] 'a|b' contains the shell metacharacter "
                "'|' (banned: ; | & < > ` $ ( ) [ ] and newline/CR/tab/NUL), dropped"
            )],
            attempt=1,
        )[0]

        decision = ReplanPolicy().decide(failure_history=[trigger], completed_attempts=1)
        context = format_replan_context(
            [trigger], attempt=2, max_attempts=3, advice=decision.advice_for_planner,
        )

        assert decision.action == "continue"
        assert "REMOVED before it ran" in decision.advice_for_planner
        assert "'a|b'" in context
        assert "Do not resend the same arguments" in context


class TestBracesAreSearchable:
    def test_the_sanitiser_lets_a_brace_search_through(self, tmp_path: Path):
        sources, warnings = _sanitize(tmp_path, [{
            "tool": "shell_exec",
            "arguments": {"argv": ["findstr", "/s", "/n", "/c:{{step:", "*.py"]},
        }])

        assert warnings == []
        assert sources[0]["arguments"]["argv"] == ["findstr", "/s", "/n", "/c:{{step:", "*.py"]

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

    def test_a_control_character_drop_names_the_control_character(self, tmp_path: Path):
        sources, warnings = _sanitize(tmp_path, [{
            "tool": "shell_exec",
            "arguments": {"argv": ["findstr", "a\nb", "*.py"]},
        }])

        assert sources == []
        assert "metacharacter '\\n'" in warnings[0]
