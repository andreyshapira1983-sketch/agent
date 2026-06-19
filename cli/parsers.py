"""Argument parsers and small text helpers for the REPL command layer.

These are pure, dependency-light functions split out of ``main.py`` to keep
the entry point focused on the REPL loop and command dispatch. ``main.py``
re-exports every name below, so ``from main import _parse_remember`` (and the
rest) keep working exactly as before.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from core.ingestion import DEFAULT_PROJECT_LIMIT


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "approve"}


def _parse_remember(rest: str) -> tuple[list[str], str]:
    """Parse `:remember [tag,tag] text...` into (tags, content).

    Tags are detected when the first whitespace-separated token contains a
    comma OR matches a known consent tag. Anything else is treated as part
    of the content with the default tag list.

    ASCII-only identifier policy applies to TAGS (they are identifiers).
    `content` may contain any unicode (it's human text).
    """
    rest = rest.strip()
    if not rest:
        return [], ""
    head, _, tail = rest.partition(" ")
    tag_candidates = {
        "preference", "fact", "decision", "insight",
        "user-approved", "project",
    }
    if "," in head or head.lower() in tag_candidates:
        raw_tags = [t.strip().lower() for t in head.split(",") if t.strip()]
        # Silently drop non-ASCII tags so the user gets the obvious
        # fallback (`user-approved`) instead of a stack trace. Tags are
        # programming identifiers in this codebase; Russian / other
        # unicode belongs in the content body.
        tags = [t for t in raw_tags if t.isascii()]
        if not tags:
            tags = ["user-approved"]
        return tags, tail.strip()
    return ["user-approved"], rest


def _resolve_workspace_text_file(workspace: Path, raw_path: str, *, role: str) -> Path:
    if not raw_path or not raw_path.isascii():
        raise ValueError(f"{role} must be a non-empty ASCII path")
    p = Path(raw_path)
    if p.is_absolute() or ".." in p.parts:
        raise PermissionError(f"{role} must stay inside the workspace")
    resolved = (workspace / p).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        raise PermissionError(f"{role} resolves outside the workspace") from None
    if not resolved.is_file():
        raise FileNotFoundError(f"{role} not found: {raw_path}")
    return resolved


def _parse_repair_generation_args(rest: str) -> tuple[str | None, tuple[str, ...], str | None, str | None, str | None]:
    tokens = rest.split()
    if not tokens:
        return None, (), None, None, "Usage: :propose-repair <target_path> [test_path...] [--pattern PAT] [--trace TRACE]"

    pattern: str | None = None
    trace_id: str | None = None
    for flag_name in ("--pattern", "--trace"):
        while flag_name in tokens:
            i = tokens.index(flag_name)
            if i + 1 >= len(tokens):
                return None, (), None, None, f"Usage: {flag_name} requires a value"
            value = tokens[i + 1]
            tokens = tokens[:i] + tokens[i + 2:]
            if flag_name == "--pattern":
                pattern = value
            else:
                trace_id = value

    target_path = tokens[0]
    test_paths = tuple(tokens[1:] or ["tests"])
    return target_path, test_paths, pattern, trace_id, None


def _split_meta_args(rest: str) -> list[str]:
    if not rest.strip():
        return []
    try:
        tokens = shlex.split(rest, posix=False)
    except ValueError:
        tokens = rest.split()
    return [t.strip().strip('"').strip("'") for t in tokens if t.strip()]


def _parse_ingest_options(
    rest: str,
    *,
    default_path: str | None,
) -> tuple[str | None, bool, bool | None, int, str | None]:
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = DEFAULT_PROJECT_LIMIT
    paths: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                return None, dry_run, auto_write, limit, "Usage: --limit requires a number"
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                return None, dry_run, auto_write, limit, "Usage: --limit requires a number"
            if limit < 1:
                return None, dry_run, auto_write, limit, "Usage: --limit must be >= 1"
            i += 2
            continue
        paths.append(token)
        i += 1

    if not paths and default_path is None:
        return None, dry_run, auto_write, limit, "Usage: :ingest-source <path> [--dry-run] [--write-memory|--no-memory]"
    path = " ".join(paths) if paths else default_path
    return path, dry_run, auto_write, limit, None


def _parse_source_planning_args(rest: str, *, usage: str) -> tuple[bool, int, str] | None:
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 8
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print(f"Usage: {usage} --limit requires a number", file=sys.stderr)
                return None
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print(f"Usage: {usage} --limit requires a number", file=sys.stderr)
                return None
            i += 2
            continue
        goal_parts.append(token)
        i += 1
    if limit < 1:
        print(f"Usage: {usage} --limit must be >= 1", file=sys.stderr)
        return None
    return as_json, limit, " ".join(goal_parts).strip()


def _compact_one_line(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _truncate_text(text: str, max_chars: int) -> str:
    """Trim ``text`` to ``max_chars``, appending an ellipsis when shortened."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"
