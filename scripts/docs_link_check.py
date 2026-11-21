"""Check that relative Markdown links in README, docs/ and knowledge/ point at real files."""
from __future__ import annotations

import os
import re
from collections.abc import Sequence

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Markdown inline link target: the (...) in [label](target)
_LINK_RE = re.compile(r"\]\(([^)]+)\)")
# a trailing :12 or :12-34 line anchor
_LINE_ANCHOR_RE = re.compile(r":\d+(?:-\d+)?$")


def _doc_files() -> list[str]:
    """Every Markdown file the repository owns: root README, ``docs/``, ``knowledge/``."""
    files: list[str] = []
    readme = os.path.join(_ROOT, "README.md")
    if os.path.isfile(readme):
        files.append(readme)
    for tree in ("docs", "knowledge"):
        for base, _dirs, names in os.walk(os.path.join(_ROOT, tree)):
            for name in names:
                if name.endswith(".md"):
                    files.append(os.path.join(base, name))
    return files


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "tel:", "#"))


def _normalize_target(target: str) -> str:
    """Strip a ``#fragment`` and a ``:LINE`` anchor (line numbers drift; the file must exist)."""
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0]
    target = _LINE_ANCHOR_RE.sub("", target)
    return target.strip()


def _looks_like_path(target: str) -> bool:
    """Only validate things that look like file paths, not bare words."""
    return "/" in target or "." in os.path.basename(target)


def find_broken_links(files: Sequence[str] | None = None) -> list[tuple[str, str]]:
    """Return ``(source_relpath, raw_target)`` for every unresolved link."""
    broken: list[tuple[str, str]] = []
    for path in (files if files is not None else _doc_files()):
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        for raw in _LINK_RE.findall(text):
            if _is_external(raw):
                continue
            target = _normalize_target(raw)
            if not target or not _looks_like_path(target):
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not os.path.exists(resolved):
                broken.append((os.path.relpath(path, _ROOT), raw))
    return broken


def main() -> int:
    broken = find_broken_links()
    print("Docs relative-link check (read-only)")
    if not broken:
        print("  RESULT: all relative Markdown links resolve.")
        return 0
    print(f"  RESULT: {len(broken)} broken relative link(s):")
    for src, target in broken:
        print(f"    {src}  ->  {target}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
