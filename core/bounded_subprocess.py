"""Run a subprocess with a timeout that kills the whole process tree and never re-reads the pipes.
Not `subprocess.run(timeout=)`: it kills only the direct child and, on Windows, then drains
the pipes untimed, so a grandchild holding stdout hangs it."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — fixed argv from callers; never a shell
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: Max time the tree kill may take before the caller stops waiting for it.
TREE_KILL_GRACE_SECONDS = 5.0


def kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill `proc` and every descendant without blocking on a pipe (descendants first, while the parent can still be walked)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # `taskkill /T` walks the parent-pid chain.
        taskkill = shutil.which("taskkill") or "taskkill"
        try:
            subprocess.run(  # nosemgrep  # noqa: S603  # nosec B603 — fixed argv, our own child's pid
                [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=TREE_KILL_GRACE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        import signal

        try:
            # start_new_session=True made the pid its own process-group id.
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=TREE_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def run_with_tree_kill(
    run_argv: list[str], *, cwd: Path | str, env: Mapping[str, str] | None, timeout: float,
) -> tuple[bytes, bytes, int | None, bool]:
    """Run `run_argv`, returning `(stdout, stderr, exit_code, timed_out)`; `env=None` inherits the environment."""
    popen_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        # Own session so killpg reaches every descendant; Windows relies on taskkill instead.
        popen_kwargs["start_new_session"] = True
    # Not `with Popen(...)`: its `__exit__` waits without a timeout.
    proc = subprocess.Popen(  # nosemgrep  # noqa: S603  # nosec B603 — argv fixed by the caller; shell=False  # pylint: disable=consider-using-with
        run_argv,
        cwd=cwd,
        env=None if env is None else dict(env),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(proc)
        return exc.stdout or b"", exc.stderr or b"", None, True
    except KeyboardInterrupt:
        kill_process_tree(proc)
        raise
    return stdout_bytes or b"", stderr_bytes or b"", proc.returncode, False
