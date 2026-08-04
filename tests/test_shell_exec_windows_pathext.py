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

import os
import sys
from pathlib import Path

import pytest

from tools.shell_exec import ShellExecTool


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT is Windows-only")
def test_pathext_is_passed_through(tmp_path: Path, monkeypatch):
    """Set the variable explicitly — the test must not depend on the shell it runs in.

    The first version of this test asserted `"PATHEXT" in _safe_env()` and read
    the variable straight from the ambient environment. It was green in a
    developer terminal and RED when the agent ran the suite through its own
    `run_tests` tool, which strips the environment down and (until this change)
    dropped PATHEXT — the live agent found this failure before I did.
    """
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT")
    tool = ShellExecTool(workspace_root=tmp_path)

    assert tool._safe_env().get("PATHEXT") == ".COM;.EXE;.BAT"


def test_pathext_is_not_invented_when_absent(tmp_path: Path, monkeypatch):
    """Forwarding means passing on what exists, never fabricating a value."""
    monkeypatch.delenv("PATHEXT", raising=False)
    tool = ShellExecTool(workspace_root=tmp_path)

    assert "PATHEXT" not in tool._safe_env()


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT is Windows-only")
def test_a_name_on_path_can_actually_be_resolved(tmp_path: Path, monkeypatch):
    """The end-to-end shape of the live failure: `where` must find what exists."""
    monkeypatch.setenv("PATHEXT", os.environ.get("PATHEXT") or ".COM;.EXE;.BAT")
    tool = ShellExecTool(workspace_root=tmp_path)

    result = tool.run(argv=["where", "where"])
    payload = result if isinstance(result, dict) else getattr(result, "output", {})

    assert payload.get("exit_code") == 0, (
        f"`where where` could not resolve a binary that certainly exists: {payload}"
    )
    assert "where" in str(payload.get("stdout", "")).lower()


def test_the_env_stays_minimal(tmp_path: Path):
    """Widening the env is a security decision, so pin exactly what is allowed."""
    tool = ShellExecTool(workspace_root=tmp_path)
    allowed = {"PATH", "PATHEXT", "SystemRoot", "HOME", "USERPROFILE",
               "HOMEDRIVE", "HOMEPATH"}

    assert set(tool._safe_env()) <= allowed
