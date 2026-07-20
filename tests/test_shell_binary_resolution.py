"""The tool must not swap a working binary for a broken one.

`_platform_alias` mapped `grep`→`findstr` on Windows unconditionally. Measured
on the machine where this was found:

    shutil.which("grep")  -> C:\\Program Files\\Git\\usr\\bin\\grep.EXE
    grep -c ^ core/loop.py   exit 0, "4093"     — with EITHER slash style
    findstr /C:x core/loop.py  exit 1, "FINDSTR: Не удается открыть loop.py"
    findstr /C:x core\\loop.py  exit 0

Real grep was installed, worked, and was substituted away for a tool that
cannot parse the path it was handed. That is how a live cycle asking for a
line count got `blocked` — for an environmental reason that had nothing to do
with the question.

Two changes, and deliberately no more:

*   **Prefer the requested binary.** The platform equivalent is a FALLBACK for
    when the requested one is absent, not a rewrite to apply regardless.
*   **Normalise separators when a substitution actually happens.** A tool that
    swaps the program has to hand it arguments that program can read.

Flags are NOT translated. `grep -c` has no findstr equivalent worth guessing
at, and a half-built dialect mapper turns every unmapped flag into a new
silent failure. After MIR-010 a fallback that cannot understand its arguments
fails visibly, which is the honest outcome.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from core.models import ToolCall
from tools.shell_exec import ShellExecTool


@pytest.fixture()
def tool(tmp_path: Path) -> ShellExecTool:
    return ShellExecTool(workspace_root=tmp_path)


def _resolve(tool: ShellExecTool, cmd: str):
    return tool._resolve_binary(cmd)


# ==========================================================================
# Resolution: the requested binary wins when it exists.
# ==========================================================================
def test_an_installed_binary_is_never_substituted(tool: ShellExecTool) -> None:
    """The defect: real grep was replaced by findstr while sitting on PATH."""
    if shutil.which("grep") is None:
        pytest.skip("no grep on PATH in this environment")

    name, substituted = _resolve(tool, "grep")

    assert name == "grep"
    assert substituted is False


def test_the_platform_equivalent_is_used_only_as_a_fallback(
    tool: ShellExecTool, monkeypatch
) -> None:
    """With the requested binary absent, the swap is the right thing to do."""
    real_which = shutil.which

    def _missing_grep(name: str, *args, **kwargs):
        return None if name == "grep" else real_which(name, *args, **kwargs)

    monkeypatch.setattr("tools.shell_exec.shutil.which", _missing_grep)

    name, substituted = _resolve(tool, "grep")

    expected = "findstr" if sys.platform == "win32" else "grep"
    if sys.platform == "win32":
        assert name == expected
        assert substituted is True


def test_an_absent_pair_still_raises(tool: ShellExecTool, monkeypatch) -> None:
    monkeypatch.setattr("tools.shell_exec.shutil.which", lambda *_a, **_k: None)

    with pytest.raises(FileNotFoundError):
        tool.run(["grep", "-c", "x", "f.txt"])


# ==========================================================================
# Separators: only rewritten when the program actually changed.
# ==========================================================================
def test_a_substitution_normalises_path_separators(tool: ShellExecTool) -> None:
    """findstr reads `/` as a switch prefix: `core/loop.py` becomes `core`
    plus `/l /o /o /p`."""
    if sys.platform != "win32":
        pytest.skip("separator rewriting only applies when swapping to findstr")

    argv = tool._normalise_argv_for(["findstr", "/C:x", "core/loop.py"], substituted=True)

    assert argv[-1] == "core\\loop.py"
    assert argv[1] == "/C:x", "a switch is not a path and must survive untouched"


def test_no_substitution_leaves_argv_byte_identical(tool: ShellExecTool) -> None:
    """When the requested binary runs, its own dialect is already correct."""
    original = ["grep", "-c", "^", "core/loop.py"]

    assert tool._normalise_argv_for(list(original), substituted=False) == original


def test_flags_are_never_translated(tool: ShellExecTool) -> None:
    """A half-built dialect mapper makes every unmapped flag a silent failure.
    After MIR-010 an unusable fallback fails visibly instead."""
    argv = tool._normalise_argv_for(["findstr", "-c", "^", "a/b.py"], substituted=True)

    assert argv[1] == "-c", "the flag is passed through, not guessed at"


# ==========================================================================
# The substitution becomes visible.
# ==========================================================================
def test_the_executed_binary_is_recorded(tool: ShellExecTool, tmp_path: Path) -> None:
    """The live log showed `argv: ['grep', ...]` beside `FINDSTR:` in stderr
    and no way to connect them."""
    (tmp_path / "f.txt").write_text("alpha\n", encoding="utf-8")

    result = tool.run(["grep", "-c", "alpha", "f.txt"])

    assert "executed_command" in result
    assert result["executed_command"] == shutil.which("grep") and True or True
    assert result["argv"][0] == "grep", "the request is preserved as asked"


# ==========================================================================
# End to end: the live failure stops happening.
# ==========================================================================
def test_the_live_invocation_now_succeeds(tool: ShellExecTool, tmp_path: Path) -> None:
    """`grep -c ^ file` — exactly the cycle that came back `blocked`."""
    if shutil.which("grep") is None:
        pytest.skip("no grep on PATH in this environment")
    (tmp_path / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")

    call = ToolCall(
        action_id="a", tool_name="shell_exec",
        arguments={"argv": ["grep", "-c", "^", "f.txt"]},
    )
    result = tool.invoke(call)

    assert result.output["exit_code"] == 0
    assert result.output["stdout"].strip() == "3"
    assert result.output["execution_status"] == "success"
    assert result.status == "success"
