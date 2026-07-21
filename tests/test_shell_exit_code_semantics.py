"""A command that failed must not be reported as a step that succeeded.

Observed live (MIR-010): `grep -c ^ core/loop.py` came back with

    exit_code: 1, stderr: "FINDSTR: Cannot open loop.py"
    tool_result  status=success
    verify       ok=True

The command could not open the file, the tool called it a success, and the
verifier agreed. `shell_exec` sets the status from "did the subprocess run",
`exit_code` lives inside the output dict, and nothing reads it.

The obvious fix — any non-zero exit is a failure — is wrong, and the audit said
so before this was attempted. Measured on this machine:

    grep found        exit 0   stderr empty
    grep no match     exit 1   stderr empty      <- a legitimate ANSWER
    grep missing file exit 2   stderr non-empty
    where not found   exit 1   stderr NON-empty  <- also a legitimate answer

So `where` writes diagnostics on a perfectly normal negative result, and a
blanket rule would call that a failure while a family-blind stderr heuristic
would too. Each family gets its own contract, and two facts stay separate:

    execution_status   did the command run correctly
    answer_result      what it answered — positive, negative, not_applicable

"grep found nothing" is a successful execution with a negative answer. That is
not the same fact as "grep could not open the file", and collapsing them is
what produced the live defect.
"""
from __future__ import annotations

import pytest

from tools.shell_exec import ShellExecTool, classify_shell_result


def _classify(command: str, exit_code: int | None, stderr: str = ""):
    return classify_shell_result(command, exit_code=exit_code, stderr=stderr)


# ==========================================================================
# Per-family contracts, each measured rather than assumed.
# ==========================================================================
@pytest.mark.parametrize("command", ["grep", "egrep", "fgrep"])
class TestGrepFamily:
    def test_match_is_a_positive_answer(self, command: str) -> None:
        assert _classify(command, 0) == ("success", "positive")

    def test_no_match_with_a_clean_stderr_is_a_negative_answer(self, command: str) -> None:
        assert _classify(command, 1) == ("success", "negative")

    def test_exit_one_with_diagnostics_is_a_failure(self, command: str) -> None:
        """The live case: FINDSTR-style tools report trouble as 1 + stderr."""
        assert _classify(command, 1, "grep: core/x.py: No such file") == (
            "failure", "not_applicable"
        )

    def test_exit_two_is_a_failure(self, command: str) -> None:
        assert _classify(command, 2, "grep: No such file or directory")[0] == "failure"


class TestRipgrep:
    """`rg` documents 2 as its error code, so the exit code alone decides and
    no stderr heuristic is invented for it."""

    def test_match(self) -> None:
        assert _classify("rg", 0) == ("success", "positive")

    def test_no_match_even_with_stderr(self) -> None:
        assert _classify("rg", 1, "warning: ignoring .gitignore") == ("success", "negative")

    def test_error(self) -> None:
        assert _classify("rg", 2, "error")[0] == "failure"


class TestFindstr:
    def test_match(self) -> None:
        assert _classify("findstr", 0) == ("success", "positive")

    def test_no_match_is_a_negative_answer(self) -> None:
        assert _classify("findstr", 1) == ("success", "negative")

    def test_the_live_case_is_a_failure(self) -> None:
        """Exactly what the live log carried."""
        assert _classify("findstr", 1, "FINDSTR: Cannot open loop.py") == (
            "failure", "not_applicable"
        )

    def test_higher_codes_fail(self) -> None:
        assert _classify("findstr", 2, "")[0] == "failure"


@pytest.mark.parametrize("command", ["diff", "cmp"])
class TestDiffFamily:
    def test_equal(self, command: str) -> None:
        assert _classify(command, 0) == ("success", "positive")

    def test_different_is_an_answer_not_a_failure(self, command: str) -> None:
        assert _classify(command, 1) == ("success", "negative")

    def test_trouble_is_a_failure(self, command: str) -> None:
        assert _classify(command, 2, "diff: missing operand")[0] == "failure"


@pytest.mark.parametrize("command", ["test", "["])
class TestTest:
    def test_predicate_true(self, command: str) -> None:
        assert _classify(command, 0) == ("success", "positive")

    def test_predicate_false(self, command: str) -> None:
        assert _classify(command, 1) == ("success", "negative")

    def test_usage_error(self, command: str) -> None:
        assert _classify(command, 2, "test: too many arguments")[0] == "failure"


class TestWhereWhich:
    """Measured on this machine before being given a contract.

        where found      exit 0   stderr empty
        where not found  exit 1   stderr NON-empty  (ИНФОРМАЦИЯ: не удается найти)
        where bad flag   exit 2
        which found      exit 0
        which not found  exit 1   stderr NON-empty  (which: no zzz in (...))
        which bad option exit 255

    Both write diagnostics on a NORMAL negative result, so their contracts key
    on the exit code alone. Sharing grep's stderr rule would have turned every
    "not found" into a failure.
    """

    @pytest.mark.parametrize("command", ["where", "which"])
    def test_found(self, command: str) -> None:
        assert _classify(command, 0) == ("success", "positive")

    @pytest.mark.parametrize("command", ["where", "which"])
    def test_not_found_is_a_negative_answer_despite_stderr(self, command: str) -> None:
        assert _classify(command, 1, "which: no zzznosuch in (/usr/bin)") == (
            "success", "negative"
        )

    def test_where_bad_flag_fails(self) -> None:
        assert _classify("where", 2, "ОШИБКА. Недопустимый аргумент")[0] == "failure"

    def test_which_bad_option_fails(self) -> None:
        assert _classify("which", 255, "which: unrecognized option")[0] == "failure"


class TestUnknownCommand:
    def test_zero_succeeds(self) -> None:
        assert _classify("python", 0) == ("success", "not_applicable")

    def test_any_non_zero_fails(self) -> None:
        assert _classify("python", 1) == ("failure", "not_applicable")
        assert _classify("ls", 2)[0] == "failure"

    def test_a_timeout_is_a_failure(self) -> None:
        """`exit_code is None` means the process was killed, not that it passed."""
        assert _classify("python", None)[0] == "failure"


# ==========================================================================
# The raw facts survive, and the verifier cannot ignore a failure.
# ==========================================================================
def _output(exit_code: int, stderr: str = "", stdout: str = "") -> dict:
    return {
        "argv": ["grep", "-c", "^", "core/loop.py"],
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "timed_out": False,
        "duration_ms": 1,
        "compensation_plan": {"actions": []},
        "execution_status": classify_shell_result(
            "grep", exit_code=exit_code, stderr=stderr
        )[0],
        "answer_result": classify_shell_result(
            "grep", exit_code=exit_code, stderr=stderr
        )[1],
    }


def test_validate_output_answers_only_the_schema_question(tmp_path) -> None:
    """A failed command is a VALID report of a failure, not a broken object.

    Collapsing the two would conflate "the tool returned a correct message
    saying the command failed" with "the tool returned something malformed",
    and only the second is a validation problem. The failure is carried by
    `execution_status` and surfaces as the tool-result status instead.
    """
    tool = ShellExecTool(workspace_root=tmp_path)

    failed_ok, _ = tool.validate_output(_output(1, "FINDSTR: Cannot open loop.py"))
    negative_ok, _ = tool.validate_output(_output(1, ""))

    assert failed_ok is True, "structurally sound — the failure is reported, not malformed"
    assert negative_ok is True
    assert tool.validate_output({"argv": ["grep"]})[0] is False, "a real schema breach"


def _no_match_argv(name: str) -> list[str]:
    """A genuine "found nothing" invocation for the binary that will RUN.

    `_platform_alias` swaps `grep`->`findstr` on Windows, and POSIX flags do
    not survive that swap: `grep -c` reaches findstr as an unknown option and
    fails for real. That mismatch is its own defect, registered separately;
    here the test simply speaks the dialect of the binary it will get.
    """
    import sys as _sys

    if _sys.platform == "win32":
        return ["findstr", "/C:zzznosuchstring", name]
    return ["grep", "-c", "zzznosuchstring", name]


def test_the_raw_facts_are_preserved(tmp_path) -> None:
    """Normalisation adds fields; it never replaces what was observed."""
    tool = ShellExecTool(workspace_root=tmp_path)
    result = tool.run(["git", "rev-parse", "--verify", "zzznosuchref"])

    assert result["exit_code"] not in (0, None), "precondition: the command must fail"
    assert result["stderr"], "the diagnostics survive verbatim"
    assert result["execution_status"] == "failure"
    assert result["answer_result"] == "not_applicable"


def test_a_real_negative_answer_end_to_end(tmp_path) -> None:
    (tmp_path / "f.txt").write_text("alpha\n", encoding="utf-8")
    tool = ShellExecTool(workspace_root=tmp_path)

    result = tool.run(_no_match_argv("f.txt"))

    assert result["exit_code"] == 1
    assert result["execution_status"] == "success"
    assert result["answer_result"] == "negative"
    assert tool.validate_output(result)[0] is True


def test_a_multiplexer_falls_back_to_the_unknown_contract() -> None:
    """`git` is one binary with per-subcommand exit semantics — `diff` means
    something different from `rev-parse`. Guessing a family from the
    subcommand would be exactly the inference this design refuses."""
    assert _classify("git", 0) == ("success", "not_applicable")
    assert _classify("git", 1)[0] == "failure"
    assert _classify("git", 128)[0] == "failure"


def test_the_executed_binary_decides_not_the_requested_name() -> None:
    """`_platform_alias` swaps `grep`→`findstr` on Windows, so the exit code
    observed follows the binary that actually ran. Classifying the requested
    name would read one tool's code against another's contract."""
    import sys as _sys
    from pathlib import Path as _P

    tool = ShellExecTool(workspace_root=_P("."))
    expected = "findstr" if _sys.platform == "win32" else "grep"
    assert tool._platform_alias("grep") == expected


# ==========================================================================
# Integration through the public path — this is what reproduces the defect.
# ==========================================================================
def _invoke(tmp_path, argv: list[str]):
    from core.models import ToolCall

    tool = ShellExecTool(workspace_root=tmp_path)
    call = ToolCall(action_id="act-test", tool_name="shell_exec", arguments={"argv": argv})
    return tool, tool.invoke(call)


def test_a_real_execution_failure_is_reported_as_a_failure(tmp_path) -> None:
    """The live defect, reproduced end to end.

    Before: a non-zero exit with diagnostics on stderr produced a structurally
    valid output, `[RES] tool_result status=success`, and `verify ok=True` —
    the command failed and every layer above it said otherwise.
    """
    tool, result = _invoke(
        tmp_path, ["git", "rev-parse", "--verify", "zzznosuchref"]
    )

    assert result.output["exit_code"] not in (0, None), "precondition: it must fail"
    assert tool.validate_output(result.output)[0] is True, "the report is well-formed"
    assert result.output["execution_status"] == "failure"
    assert result.status == "error", (
        "the top-level status was the line that hid this in the log"
    )


def test_a_negative_answer_stays_a_success(tmp_path) -> None:
    """The other half of the contract: a command that correctly answered
    'no' must not be recast as broken."""
    (tmp_path / "f.txt").write_text("alpha\n", encoding="utf-8")

    tool, result = _invoke(tmp_path, _no_match_argv("f.txt"))

    assert result.output["exit_code"] == 1
    assert result.output["execution_status"] == "success"
    assert result.output["answer_result"] == "negative"
    assert result.status == "success", "a negative answer is still a completed command"
    assert tool.validate_output(result.output)[0] is True
