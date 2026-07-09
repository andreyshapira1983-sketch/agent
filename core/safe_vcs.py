"""Narrow, safe VCS helper for the trusted self-apply lane (TD-023).

This is deliberately NOT a general shell. ``tools/shell_exec.py`` keeps git
read-only (log/diff/status); the trusted self-apply lane needs a *small, fixed*
set of local write operations — create a temp branch, inspect status, commit
locally, and roll back within the lane — without ever exposing arbitrary shell
or a ``push``. Every method maps to exactly one hard-coded ``git`` argv; branch
names are validated; there is intentionally no push / fetch / remote method at
all, so the lane physically cannot reach the network.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Branch names are generated internally, but validate anyway: no shell
# metacharacters, no leading dash, no ``..`` path tricks.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")

# Never delete or hard-target these, even if asked.
_PROTECTED_BRANCHES = frozenset({"main", "master", "HEAD"})


class VcsError(RuntimeError):
    """Raised when a git invocation fails or an argument is rejected."""


@dataclass
class VcsResult:
    returncode: int
    stdout: str
    stderr: str


def _default_runner(argv: list[str], *, cwd: Path) -> VcsResult:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        shell=False,
        text=True,
        check=False,
    )
    return VcsResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _validate_branch(name: str) -> str:
    if not isinstance(name, str) or not _BRANCH_RE.match(name) or ".." in name:
        raise VcsError(f"unsafe branch name: {name!r}")
    return name


@dataclass
class SafeVCS:
    """A minimal git front-end scoped to one workspace.

    All state-changing operations are local. There is no ``push``, ``fetch``,
    ``pull`` or ``remote`` method by design.
    """

    workspace: Path
    runner: Callable[..., VcsResult] = _default_runner
    author_name: str = "Self-Apply Lane"
    author_email: str = "self-apply@localhost"

    # -- internal ---------------------------------------------------------
    def _git(self, *args: str) -> VcsResult:
        argv = ["git", *args]
        result = self.runner(argv, cwd=self.workspace)
        if result.returncode != 0:
            raise VcsError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    # -- read -------------------------------------------------------------
    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def head_hash(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def status_porcelain(self) -> str:
        return self._git("status", "--porcelain").stdout

    def is_clean(self) -> bool:
        return self.status_porcelain().strip() == ""

    # -- branch -----------------------------------------------------------
    def create_temp_branch(self, name: str) -> None:
        _validate_branch(name)
        self._git("checkout", "-b", name)

    def checkout(self, name: str) -> None:
        _validate_branch(name)
        self._git("checkout", name)

    def delete_branch(self, name: str) -> None:
        _validate_branch(name)
        if name in _PROTECTED_BRANCHES:
            raise VcsError(f"refusing to delete protected branch {name!r}")
        self._git("branch", "-D", name)

    # -- working tree / commit -------------------------------------------
    def stage_all(self) -> None:
        self._git("add", "-A")

    def commit(self, message: str) -> str:
        if not isinstance(message, str) or not message.strip():
            raise VcsError("commit message must be a non-empty string")
        # Author identity is injected per-commit so the lane never depends on
        # (or mutates) global git config.
        self._git(
            "-c", f"user.name={self.author_name}",
            "-c", f"user.email={self.author_email}",
            "commit", "-m", message,
        )
        return self.head_hash()

    def reset_hard(self) -> None:
        self._git("reset", "--hard", "HEAD")

    def clean_untracked(self) -> None:
        self._git("clean", "-fd")
