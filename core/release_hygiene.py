"""Release artifact hygiene checks.

Runtime redaction protects prompts/logs/output. Release hygiene protects the
package boundary: local secrets and developer artifacts must never be included
in an archive handed to another person or machine.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


FORBIDDEN_RELEASE_NAMES = {
    ".env",
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "credentials.json",
    "token.json",
    "client_secret.json",
}

FORBIDDEN_RELEASE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".coverage",
    ".pem",
    ".key",
)

DEFAULT_RELEASE_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "logs",
    "data",
}

DEFAULT_RELEASE_EXCLUDE_FILES = {
    ".env",
    ".coverage",
    "credentials.json",
    "token.json",
    "client_secret.json",
}


@dataclass(frozen=True)
class ReleaseHygieneReport:
    workspace: str
    ok: bool
    files_included: int
    files_excluded: int
    forbidden_present: tuple[str, ...] = ()
    forbidden_included: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "ok": self.ok,
            "files_included": self.files_included,
            "files_excluded": self.files_excluded,
            "forbidden_present": list(self.forbidden_present),
            "forbidden_included": list(self.forbidden_included),
            "warnings": list(self.warnings),
        }

    def user_summary(self) -> str:
        lines = [
            "=== release hygiene ===",
            f"ok: {self.ok}",
            f"files: included={self.files_included} excluded={self.files_excluded}",
        ]
        if self.forbidden_present:
            lines.append("forbidden local artifacts present but excluded:")
            lines.extend(f"  - {path}" for path in self.forbidden_present[:20])
        if self.forbidden_included:
            lines.append("forbidden files that would be included:")
            lines.extend(f"  - {path}" for path in self.forbidden_included[:20])
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        return "\n".join(lines)


@dataclass(frozen=True)
class ReleaseManifest:
    root: Path
    include_files: tuple[Path, ...]
    excluded_files: tuple[Path, ...]
    forbidden_present: tuple[Path, ...]
    forbidden_included: tuple[Path, ...]
    warnings: tuple[str, ...] = ()

    def report(self) -> ReleaseHygieneReport:
        return ReleaseHygieneReport(
            workspace=str(self.root),
            ok=not self.forbidden_included,
            files_included=len(self.include_files),
            files_excluded=len(self.excluded_files),
            forbidden_present=tuple(_rel(self.root, path) for path in self.forbidden_present),
            forbidden_included=tuple(_rel(self.root, path) for path in self.forbidden_included),
            warnings=self.warnings,
        )


def build_release_manifest(
    workspace: Path,
    *,
    extra_exclude_dirs: Iterable[str] = (),
    extra_exclude_files: Iterable[str] = (),
) -> ReleaseManifest:
    root = workspace.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"release workspace is not a directory: {workspace}")

    exclude_dirs = set(DEFAULT_RELEASE_EXCLUDE_DIRS) | set(extra_exclude_dirs)
    exclude_files = set(DEFAULT_RELEASE_EXCLUDE_FILES) | set(extra_exclude_files)
    include_files: list[Path] = []
    excluded_files: list[Path] = []
    forbidden_present: list[Path] = []
    forbidden_included: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            path = current / dirname
            rel_parts = path.relative_to(root).parts
            if _is_forbidden_path(path):
                forbidden_present.append(path)
            if _is_excluded(rel_parts, path, exclude_dirs=exclude_dirs, exclude_files=exclude_files):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            path = current / filename
            rel_parts = path.relative_to(root).parts
            if _is_forbidden_path(path):
                forbidden_present.append(path)
            if _is_excluded(rel_parts, path, exclude_dirs=exclude_dirs, exclude_files=exclude_files):
                excluded_files.append(path)
                continue
            include_files.append(path)
            if _is_forbidden_path(path):
                forbidden_included.append(path)

    warnings = []
    if forbidden_present:
        warnings.append(
            "local forbidden artifacts exist; release packaging must keep them excluded"
        )
    if forbidden_included:
        warnings.append("release manifest would include forbidden artifacts")

    return ReleaseManifest(
        root=root,
        include_files=tuple(include_files),
        excluded_files=tuple(excluded_files),
        forbidden_present=tuple(sorted(forbidden_present)),
        forbidden_included=tuple(sorted(forbidden_included)),
        warnings=tuple(warnings),
    )


def _is_excluded(
    rel_parts: tuple[str, ...],
    path: Path,
    *,
    exclude_dirs: set[str],
    exclude_files: set[str],
) -> bool:
    if any(part in exclude_dirs for part in rel_parts):
        return True
    if path.name in exclude_files:
        return True
    if any(path.name.endswith(suffix) for suffix in FORBIDDEN_RELEASE_SUFFIXES):
        return True
    return False


def _is_forbidden_path(path: Path) -> bool:
    if path.name in FORBIDDEN_RELEASE_NAMES:
        return True
    if any(path.name.endswith(suffix) for suffix in FORBIDDEN_RELEASE_SUFFIXES):
        return True
    return any(part in FORBIDDEN_RELEASE_NAMES for part in path.parts)


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
