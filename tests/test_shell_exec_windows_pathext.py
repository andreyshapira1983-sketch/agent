"""On Windows a name without PATHEXT resolves to nothing — including the truth.

Live run 2026-08-04. The operator told the agent its tools were all connected;
the agent, instead of taking that on trust, ran a real check —
`shell_exec(['where', 'python'])` — and got `exit_code=1`,
"Could not find files for the given pattern(s)". It then reported that the
declared connectivity and the actual availability of its tools "are different
things".

Nothing was wrong with the tools. `_safe_env` passed PATH, SystemRoot and the
git identity variables, but **not PATHEXT**, and on Windows the executable
resolver needs PATHEXT to try `python.exe` for the name `python`. Measured with
the same PATH, only the variable differing:

    without PATHEXT -> exit 1, empty
    with    PATHEXT -> exit 0, C:\\...\\Python311\\python.exe

So the agent's own instrument lied to it about its own environment, and the
agent believed the instrument — which is exactly what it should do. The defect
is here, not in its reasoning.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.shell_exec import ShellExecTool


@pytest.fixture()
def tool(tmp_path: Path) -> ShellExecTool:
    return ShellExecTool(workspace_root=tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT is Windows-only")
def test_pathext_is_passed_through(tool: ShellExecTool):
    assert "PATHEXT" in tool._safe_env()


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT is Windows-only")
def test_a_name_on_path_can_actually_be_resolved(tool: ShellExecTool):
    """The end-to-end shape of the live failure: `where` must find what exists."""
    result = tool.run(argv=["where", "where"])
    payload = result if isinstance(result, dict) else getattr(result, "output", {})

    assert payload.get("exit_code") == 0, (
        f"`where where` could not resolve a binary that certainly exists: {payload}"
    )
    assert "where" in str(payload.get("stdout", "")).lower()


def test_the_env_stays_minimal(tool: ShellExecTool):
    """Widening the env is a security decision, so pin exactly what is allowed."""
    allowed = {"PATH", "PATHEXT", "SystemRoot", "HOME", "USERPROFILE",
               "HOMEDRIVE", "HOMEPATH"}

    assert set(tool._safe_env()) <= allowed
