"""Ingestion helpers: workspace-confined path resolution, project file walking, and text chunking.

Extracted from `core/ingestion` by autonomous self-build module split.
"""
from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.ingestion_reports import IngestReport
from core.source_library import SourceLibraryEntry

TEXT_EXTENSIONS = frozenset({
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
})

SKIP_DIR_NAMES = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "logs",
    "node_modules",
    "procedural_chroma",
    "venv",
})

CHUNK_CHARS = 800


def _candidate_urls(search_output: Any, *, entry: SourceLibraryEntry) -> list[str]:
    if not isinstance(search_output, list):
        return []
    urls: list[str] = []
    for item in search_output:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "").strip()
        if not url:
            continue
        if not _is_http_url(url):
            continue
        if not entry.allows_url(url):
            continue
        urls.append(url)
    return urls


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolve_inside_workspace(workspace: Path, raw: str) -> Path:
    if not raw or not raw.strip():
        raise ValueError("path is required")
    workspace = workspace.resolve()
    candidate = Path(raw.strip().strip('"').strip("'"))
    target = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    _ensure_inside_workspace(workspace, target)
    return target


def _ensure_inside_workspace(workspace: Path, target: Path) -> None:
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(f"Path escapes workspace: {target}") from exc


def _iter_project_files(root: Path, *, limit: int) -> Iterable[Path]:
    yielded = 0
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix().casefold()):
        if yielded >= limit:
            break
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        yield path
        yielded += 1


def _chunk_python(text: str, *, max_chunks: int) -> list[str]:
    """Knowledge from a Python file: what it SAYS about itself, not its lines.

    `_chunk_text` walks paragraphs and accumulates them up to `CHUNK_CHARS`,
    which gives readable passages for prose and blobs of code for a source
    file. Measured on a live learning run 2026-08-05: the planner chose five
    test files and the pipeline extracted 34 claims, the first of which was
    `_touch(tmp_path / "core" / ...)`. Nothing there is knowledge about the
    project; it is scaffolding.

    What a test carries is the behaviour it pins — its name and its docstring.
    What a module carries is its own docstring and those of its definitions.
    That is what this returns: one chunk per documented definition, prefixed
    with the qualified name so the claim says WHOSE behaviour it describes,
    plus the module docstring.

    Two outcomes that are NOT the same and were once described as one:

    * The file does not parse -> `_chunk_text`. Unparseable source is still
      text, and a syntax error anywhere in the workspace must not silently
      drop a source from learning.
    * The file parses and documents nothing -> `[]`, and the caller skips it
      with the reason recorded. A file that explains nothing about itself is
      not made informative by extracting its lines.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _chunk_text(text, max_chunks=max_chunks)

    out: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc and module_doc.strip():
        out.append(module_doc.strip())

    def walk(body: list[ast.stmt], prefix: str) -> None:
        """Every documented definition, wherever it is written.

        Descends through `if` / `try` / `with` / loops as well as through
        definitions, because a documented function under `if TYPE_CHECKING:`
        or in an `except ImportError:` fallback is as real as one at module
        level — and this repository writes both. An earlier version recursed
        only into definition bodies and lost all three shapes silently.

        The prefix grows ONLY through classes and functions: a block is not a
        namespace, so `Inline` inside a `with` is still `Inline`.
        """
        for node in body:
            if len(out) >= max_chunks:
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{node.name}"
                doc = ast.get_docstring(node)
                if doc and doc.strip():
                    out.append(f"{name}: {doc.strip()}")
                walk(node.body, f"{name}.")
                continue
            for attr in ("body", "orelse", "finalbody", "handlers"):
                nested = getattr(node, attr, None)
                if isinstance(nested, list) and nested:
                    walk([s for s in nested if isinstance(s, ast.stmt)], prefix)
                    for handler in nested:
                        if isinstance(handler, ast.ExceptHandler):
                            walk(handler.body, prefix)

    walk(tree.body, "")
    return out[:max_chunks]


def _chunk_text(text: str, *, max_chunks: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), CHUNK_CHARS):
                piece = paragraph[start:start + CHUNK_CHARS].strip()
                if piece:
                    chunks.append(piece)
                if len(chunks) >= max_chunks:
                    return chunks
            continue
        candidate = (current + "\n\n" + paragraph).strip() if current else paragraph
        if len(candidate) <= CHUNK_CHARS:
            current = candidate
        else:
            if current:
                chunks.append(current)
                if len(chunks) >= max_chunks:
                    return chunks
            current = paragraph
    if current and len(chunks) < max_chunks:
        chunks.append(current)
    return chunks[:max_chunks]


def _skip(report: IngestReport, path: Path, workspace: Path, reason: str) -> None:
    report.files_skipped += 1
    label = _relative_label(workspace, path)
    report.skipped_paths.append(f"{label} ({reason})")
    report.errors.append(f"{label}: {reason}")


def _relative_label(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        # Not an error: `relative_to` raises for any path outside the
        # workspace, and a label for such a path is legitimately the absolute
        # one. Nothing failed, so there is nothing to journal.
        return str(path)
