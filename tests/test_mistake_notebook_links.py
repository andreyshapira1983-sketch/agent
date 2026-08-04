"""Addresses in the mistake notebook must point at real places.

`docs/MISTAKE_NOTEBOOK.md` is the shared channel between the assistant and the
autonomous agent: one writes a finding with a `file:line` address, the other
walks over and looks. A broken link makes the record worthless — the manual
search it was meant to remove comes right back.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_NOTEBOOK = _REPO / "docs" / "MISTAKE_NOTEBOOK.md"

#: A link such as `[core/loop.py:123]`. Documents are addressable too: a
#: mistake can live in prose, and the guard caught exactly such a record.
_LINK_RE = re.compile(r"\[([\w/.\-]+\.(?:py|md)):(\d+)\]")

#: Header of the findings table; the journal is located by it.
_TABLE_HEADER = "| # | File:line |"


def _links() -> list[tuple[str, int]]:
    text = _NOTEBOOK.read_text(encoding="utf-8")
    return [(m.group(1), int(m.group(2))) for m in _LINK_RE.finditer(text)]


def _inside_repo(rel: str) -> bool:
    """Does the address stay inside the repository?

    The address pattern allows dots and slashes, so `../../secrets.py` passes
    it unchallenged. A link pointing outside is meaningless here, and the
    journal is appended to by the agent as well — the guard must catch it.
    """
    try:
        (_REPO / rel).resolve().relative_to(_REPO.resolve())
    except (ValueError, OSError):
        return False
    return True


def test_the_notebook_exists_and_carries_addresses():
    assert _NOTEBOOK.is_file(), "the mistake notebook is gone"
    assert _links(), "the journal holds no address — the manual search is back"


def test_no_address_escapes_the_repository():
    outside = sorted({rel for rel, _ in _links() if not _inside_repo(rel)})

    assert not outside, f"addresses point outside the repository: {outside}"


def test_the_escape_check_actually_catches_a_way_out():
    """Proof of the guard: a path leading out must be rejected."""
    assert not _inside_repo("../../../etc/passwd.py")
    assert not _inside_repo("a/../../b.py")
    assert _inside_repo("core/loop.py")


def test_every_address_points_at_a_real_line():
    broken: list[str] = []
    cache: dict[str, list[str] | None] = {}   # read each file once
    for rel, lineno in _links():
        if not _inside_repo(rel):
            broken.append(f"{rel}:{lineno} — path leads outside")
            continue
        if rel not in cache:
            path = _REPO / rel
            cache[rel] = (
                path.read_text(encoding="utf-8", errors="replace").splitlines()
                if path.is_file() else None
            )
        lines = cache[rel]
        if lines is None:
            broken.append(f"{rel}:{lineno} — no such file")
        elif not 1 <= lineno <= len(lines):
            broken.append(f"{rel}:{lineno} — no such line ({len(lines)} total)")
        elif not lines[lineno - 1].strip():
            broken.append(f"{rel}:{lineno} — blank line, the address drifted")

    assert not broken, "notebook addresses went stale:\n  " + "\n  ".join(broken)


def test_each_finding_row_names_a_place():
    """A journal row without an address is a complaint, not a finding."""
    text = _NOTEBOOK.read_text(encoding="utf-8")
    assert _TABLE_HEADER in text, (
        f"the notebook has no findings journal headed {_TABLE_HEADER!r} — "
        "the table was renamed or lost"
    )
    table = text[text.index(_TABLE_HEADER):].split("\n\n", 1)[0]
    rows = [r for r in table.splitlines() if r.startswith("| ") and "---" not in r]
    body = rows[1:]  # drop the header

    assert body, "the findings journal is empty"
    for row in body:
        assert _LINK_RE.search(row), f"journal row without an address: {row[:80]}"
