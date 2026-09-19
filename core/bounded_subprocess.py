"""Bounded subprocess: a timeout that ends the wait, not one that promises to.

Why `subprocess.run(..., timeout=)` is not enough. On expiry it kills only
the direct child and then calls `communicate()` WITHOUT a timeout to drain
the pipes. Any grandchild that inherited stdout keeps the pipe open, and that
second wait never returns.

Measured twice on 2026-09-05, both through the `cmd\\git.EXE` launcher, which
leaves a `mingw64\\bin\\git.exe` grandchild:

* `shell_exec` running `git blame` — the 30-second timeout returned after
  16 minutes, when the driver killed the whole turn;
* `core.repo_provenance._tracked_paths` running `git ls-files -z` to decide
  whether a blocked document is the agent's own — the 20-second timeout
  returned after 600 seconds, when the operator killed the grandchild by hand
  (exam session exam_k, turn 42). Until then the turn stood still after
  `injection_blocked`, and his own `core/replan.py` was withheld from him.

Here the tree is killed first and the pipes are never re-read after the
timeout: partial output that `communicate` already had is returned, and the
rest is forfeited on purpose — a bounded answer beats a complete one that
never arrives.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — fixed argv from callers; never a shell
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: How long the tree kill itself may take before the caller stops waiting for
#: it. A kill that hangs must not become the hang it was meant to prevent.
TREE_KILL_GRACE_SECONDS = 5.0


def kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill `proc` and every descendant, without ever blocking on a pipe.

    The order matters: descendants first, while the parent is still alive to
    be walked. Kill the parent first and its children are reparented, and a
    tree walk no longer finds them.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # `taskkill /T` walks the parent-pid chain; `/F` does not ask.
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
            # The child was started with start_new_session=True, so its pid
            # is also its process-group id.
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
    """Run `run_argv`, returning `(stdout, stderr, exit_code, timed_out)`.

    `env=None` inherits the caller's environment. See the module docstring
    for why this exists instead of `subprocess.run(..., timeout=)`.
    """
    popen_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        # A session of its own, so killpg reaches every descendant. Windows
        # has no process groups worth the name; taskkill walks the tree.
        popen_kwargs["start_new_session"] = True
    # Not `with Popen(...)`: `__exit__` calls `wait()` without a timeout, the
    # very unbounded wait this function exists to avoid. The child is reaped
    # by `communicate` on the normal path and by `kill_process_tree` on
    # timeout.
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
