"""The agent's own test run must not see a different world than the operator's.

Live run 2026-08-04. The agent ran the full suite through its `run_tests` tool
and reported two failures in `tests/test_shell_exec_windows_pathext.py`. The
same suite was green in the operator's terminal minutes earlier. Neither side
was lying: `run_tests._build_env` forwards PATH, PYTHONPATH, SYSTEMROOT, TEMP,
TMP, HOME and USERPROFILE — and dropped PATHEXT, so inside the agent's run any
Windows lookup of an executable BY NAME resolved to nothing.

A test tool that reports failures the developer cannot reproduce, or hides ones
they can, is worse than no test tool: it makes the agent's own verification
untrustworthy in exactly the situation where it is most needed.
"""
from __future__ import annotations

import sys

import pytest

from tools.run_tests import RunTestsTool


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT is Windows-only")
def test_pathext_reaches_the_test_subprocess(monkeypatch):
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT")

    assert RunTestsTool._build_env().get("PATHEXT") == ".COM;.EXE;.BAT"


def test_pathext_is_not_invented_when_absent(monkeypatch):
    monkeypatch.delenv("PATHEXT", raising=False)

    assert "PATHEXT" not in RunTestsTool._build_env()


def test_the_env_stays_deliberate(monkeypatch):
    """Widening what a subprocess inherits is a decision — keep it explicit."""
    monkeypatch.setenv("SOME_UNRELATED_SECRET", "should-not-travel")

    assert "SOME_UNRELATED_SECRET" not in RunTestsTool._build_env()


def test_agent_variables_still_travel(monkeypatch):
    """AGENT_* forwarding is what lets a nested run use the same provider."""
    monkeypatch.setenv("AGENT_PROBE_MARKER", "on")

    assert RunTestsTool._build_env().get("AGENT_PROBE_MARKER") == "on"
