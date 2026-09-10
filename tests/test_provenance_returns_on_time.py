"""The provenance check returns on time, and a late git means "not ours".

Exam 2026-09-05, session exam_k, turn 42. `core/replan.py` was read, the
injection guard blocked it (it quotes "ignore previous instructions" as a
pattern), and `_blocked_output_or_replan` asked `core.repo_provenance` whether
the document is the agent's own. `git ls-files -z` did not come back. The
20-second timeout of `subprocess.run` killed the `cmd\\git.EXE` launcher, the
real `git.exe` grandchild kept the pipe open, and the drain that follows the
kill waited on it for 600 seconds — until the operator killed the grandchild
by hand. The turn then finished in six seconds.

The same hole had been closed in `shell_exec` that morning; this test pins
that `repo_provenance` uses the same bounded runner and that a timeout is
read as "not committed" — the safe side, as the module's own docstring says.
"""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from core import repo_provenance
from core.bounded_subprocess import TREE_KILL_GRACE_SECONDS, run_with_tree_kill


@pytest.fixture(autouse=True)
def _fresh_cache():
    repo_provenance._TRACKED_CACHE.clear()
    yield
    repo_provenance._TRACKED_CACHE.clear()


class TestATimedOutGitMeansNotOurs:
    def test_a_timeout_is_read_as_not_committed(self, tmp_path: Path, monkeypatch):
        calls: list[list[str]] = []

        def _late(argv, *, cwd, env, timeout):
            calls.append(argv)
            return b"core/replan.py\0", b"", None, True  # partial output, timed out

        monkeypatch.setattr(repo_provenance, "run_with_tree_kill", _late)

        assert repo_provenance.is_committed_source("file:core/replan.py", tmp_path) is False
        assert calls and calls[0][:3] == ["git", "-c", "core.fsmonitor=false"], (
            "the daemon is kept out of the call"
        )

    def test_a_clean_answer_is_still_trusted(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            repo_provenance, "run_with_tree_kill",
            lambda argv, *, cwd, env, timeout: (b"core/replan.py\0docs/x.md\0", b"", 0, False),
        )
        assert repo_provenance.is_committed_source("file:core/replan.py", tmp_path) is True
        assert repo_provenance.is_committed_source("file:core/other.py", tmp_path) is False


class TestTheRunnerReturnsWhenAGrandchildHoldsThePipe:
    def test_the_wait_ends_at_the_timeout_not_at_the_grandchild(self, tmp_path: Path):
        # A child that spawns a grandchild holding stdout, then sleeps past the
        # timeout. `subprocess.run(timeout=)` would wait for the grandchild.
        script = tmp_path / "hold.py"
        script.write_text(textwrap.dedent(
            """
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            time.sleep(30)
            """
        ), encoding="utf-8")
        started = time.monotonic()
        _out, _err, code, timed_out = run_with_tree_kill(
            [sys.executable, str(script)], cwd=tmp_path, env=None, timeout=1.0,
        )
        elapsed = time.monotonic() - started
        assert timed_out is True and code is None
        assert elapsed < 1.0 + TREE_KILL_GRACE_SECONDS * 2 + 5
