"""File provenance: whether a file lies in the repository history or just in a folder."""
from __future__ import annotations

from pathlib import Path

from core.bounded_subprocess import run_with_tree_kill

_FILE_PREFIX = "file:"
_LS_FILES_TIMEOUT_S = 20

# Per-process cache: a file committed after process start counts as foreign until the end of the run, which is the safe error side.
_TRACKED_CACHE: dict[str, frozenset[str]] = {}


def _tracked_paths(workspace: Path) -> frozenset[str]:
    key = str(Path(workspace).resolve())
    cached = _TRACKED_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        # `-c core.fsmonitor=false`: with fsmonitor git contacts its daemon and a grandchild holds the pipe open.
        # The bounded run kills the whole tree on timeout (core/bounded_subprocess.py).
        stdout, _stderr, code, timed_out = run_with_tree_kill(
            ["git", "-c", "core.fsmonitor=false", "ls-files", "-z"],
            cwd=key, env=None, timeout=_LS_FILES_TIMEOUT_S,
        )
        raw = stdout.decode("utf-8", errors="replace") if code == 0 and not timed_out else ""
    except OSError:
        # No git or no repository means nothing becomes "ours"; a failed check must remove trust, not grant it.
        raw = ""
    tracked = frozenset(p for p in raw.split("\0") if p)
    _TRACKED_CACHE[key] = tracked
    return tracked


def is_committed_source(source_label: str, workspace: Path | str) -> bool:
    """Whether this output came from a file lying in the repository history.

    Label form ``file:<path>``; everything else (network, plan step, memory) or a path outside the repo is not ours."""
    label = str(source_label or "")
    if not label.startswith(_FILE_PREFIX):
        return False
    rel = label[len(_FILE_PREFIX):].strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return False
    return rel in _tracked_paths(Path(workspace))


# Tools whose output comes from our own committed tree; their blocking is softened on the same basis as a document.
# run_tests prints failing-test source, and some repo files quote injection samples on purpose.
_REPO_DERIVED_TOOLS = frozenset({"run_tests"})


def block_may_be_annotated(
    tool_name: str | None, source_label: str, workspace: Path | str
) -> bool:
    """Annotate instead of removing, only for OUR material.

    Ours means a file in the repo history or output of a tool run on that committed tree; a planted file name is neither."""
    if (tool_name or "") in _REPO_DERIVED_TOOLS:
        return True
    return is_committed_source(source_label, workspace)
