"""A path argument may not enter pytest's argv as an option.

`paths` comes from the planner, so it is model-controlled input. Every other
guard on it checks what a PATH can be — ASCII, no `..`, resolves inside the
workspace, count capped — and a string like `-p` satisfies all of them while
pytest reads it as an option. `-p <module>` makes pytest import that module,
which turns a test run into arbitrary import of anything writable.

The refusal has to be by shape (a leading dash), not by a blocklist of known
options: the dangerous set is whatever the installed pytest happens to accept.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.run_tests import RunTestsTool


@pytest.fixture
def tool(tmp_path: Path) -> RunTestsTool:
    (tmp_path / "tests").mkdir()
    return RunTestsTool(workspace_root=tmp_path)


@pytest.mark.parametrize("probe", ["-p", "-x", "--cov=/etc", "--co", "-"])
def test_a_path_that_looks_like_an_option_is_refused(tool, probe) -> None:
    with pytest.raises(PermissionError):
        tool._build_argv(paths=[probe], pattern=None)


def test_a_pattern_that_looks_like_an_option_is_refused(tool) -> None:
    """`-k` takes the next argv element as its value, so a dashed pattern
    breaks argument parsing rather than injecting — refused for the same
    reason, and so the two arguments cannot drift apart."""
    with pytest.raises(PermissionError):
        tool._build_argv(paths=None, pattern="-x")


def test_an_ordinary_path_still_passes(tool) -> None:
    argv = tool._build_argv(paths=["tests"], pattern="not slow")
    assert argv[-1] == "tests"
    assert argv[-3:-1] == ["-k", "not slow"]
