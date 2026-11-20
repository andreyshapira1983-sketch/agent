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


def _chunk_python(text: str, *, max_chars: int) -> list[str]:
    """Knowledge from a Python file: what it SAYS about itself, not its lines.

    One chunk per documented definition, prefixed with the qualified name, plus the module docstring.
    An unparseable file falls back to `_chunk_text`; a file that parses and documents nothing gives `[]` and the caller skips it.
    The budget is in CHARACTERS, not chunks, because a docstring chunk has no fixed weight.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # The same budget in the other chunker's unit (`max_chunks * CHUNK_CHARS`).
        return _chunk_text(text, max_chunks=max(1, max_chars // CHUNK_CHARS))

    out: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc and module_doc.strip():
        out.append(module_doc.strip())

    def walk(body: list[ast.stmt], prefix: str) -> None:
        """Every documented definition, wherever it is written.

        Descends through `if` / `try` / `with` / loops too (documented functions under `if TYPE_CHECKING:` or an `except ImportError:` fallback are real).
        The prefix grows ONLY through classes and functions: a block is not a namespace.
        """
        for node in body:
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

    # Trimmed after the walk, so the budget is one rule in one place rather than a stop condition in
    # the recursion.
    kept: list[str] = []
    used = 0
    for chunk in out:
        if kept and used + len(chunk) > max_chars:
            break
        # The FIRST is taken whatever it weighs: otherwise a module with one long docstring comes
        # back empty and is skipped as "documents nothing".
        kept.append(chunk)
        used += len(chunk)
    return kept


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
    except Exception:  # noqa: BLE001 — reason stated above
        # Not an error: `relative_to` raises for a path outside the workspace, and its label is then
        # the absolute path.
        return str(path)
