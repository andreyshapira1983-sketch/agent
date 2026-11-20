"""Does this text name something that exists in the workspace?"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: A token that could be a repository path; deliberately loose (existence on disk decides). Segment lengths are bounded ON PURPOSE
#: (120 chars, 40 segments): unbounded, the expression is quadratic on a long run of word characters without separators.
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_.\-]{1,120}(?:/[A-Za-z0-9_.\-]{1,120}){1,40}"
    r"|[A-Za-z0-9_\-]{1,120}\.[A-Za-z0-9]{1,5}"
)

#: Directories that are not the agent's own source, so naming a file inside one
#: says nothing about the turn being about this repository.
_IGNORED_ROOTS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4096)
def _exists(candidate: str, root: str) -> bool:
    if not candidate or candidate.startswith(("http://", "https://")):
        return False
    parts = candidate.replace("\\", "/").split("/")
    if parts and parts[0] in _IGNORED_ROOTS:
        return False
    target = Path(root) / candidate
    try:
        # `resolve` so `../` cannot walk out and report an unrelated file as ours.
        resolved = target.resolve()
        resolved.relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return resolved.exists()


def workspace_paths_named(text: str, *, root: Path | None = None) -> list[str]:
    """Paths in *text* that actually exist in the workspace."""
    base = str(root or _repo_root())
    seen: list[str] = []
    for match in _PATH_TOKEN_RE.finditer(text or ""):
        candidate = match.group(0).strip(".,;:!?»«\"'()[]")
        if candidate and candidate not in seen and _exists(candidate, base):
            seen.append(candidate)
    return seen


def names_workspace_path(text: str, *, root: Path | None = None) -> bool:
    """True when the text names at least one file or directory we own."""
    return bool(workspace_paths_named(text, root=root))
