"""Read-only guard: relative links in Markdown docs must resolve to real files.

Scans the human-facing Markdown set (root ``README.md`` + everything under
``docs/``) for Markdown links ``[text](target)`` and asserts every *relative*
target resolves to an existing file on disk. This catches the drift class where
a doc points at a path that moved or never existed (e.g. ``docs/INDEX.md``
linking ``../AGENT_DOCTRINE.md`` at the repo root while the file lives in
``docs/``).

Contract / non-goals:
- Only Markdown ``](...)`` links are checked -- not inline-code paths in prose.
- External links (``http://``, ``https://``, ``mailto:``, ``tel:``) and pure
  anchors (``#frag``) are skipped, as are bare words that do not look like paths.
- A trailing ``#anchor`` and a trailing ``:LINE`` / ``:LINE-LINE`` line anchor
  are stripped before the existence check: the file must exist, but line
  numbers are not enforced (they drift and are brittle to gate on).
- Pure read-only: no ``core`` import, no shell-out, no network access.

Exit 0 when every relative link resolves; exit 1 (and print the offenders)
otherwise. Wired into CI via ``tests/test_docs_link_check.py``.
"""
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
    """Human-facing Markdown set: root README + everything under ``docs/``."""
    files: list[str] = []
    readme = os.path.join(_ROOT, "README.md")
    if os.path.isfile(readme):
        files.append(readme)
    docs_dir = os.path.join(_ROOT, "docs")
    for base, _dirs, names in os.walk(docs_dir):
        for name in names:
            if name.endswith(".md"):
                files.append(os.path.join(base, name))
    return files


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "tel:", "#"))


def _normalize_target(target: str) -> str:
    """Strip a ``#fragment`` and a trailing ``:LINE`` anchor; return the path."""
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
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
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
