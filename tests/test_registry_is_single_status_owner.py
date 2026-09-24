"""No document outside the registry may state an issue count.

MASTER_ISSUE_REGISTRY is the single owner of issue status. That only holds
if other documents point AT it instead of copying from it — copies do not get updated, and the copy is what gets quoted.

This is not hypothetical. Before this guard: the progress tracker's "CURRENT
STATE" banner said 50 while the registry held 53, and the lifecycle contract
repeated 50 in its provenance line. Both were written as authoritative.

No exemptions are left: the one hand-listed exemption (INDEX.md) went with
that file on 2026-09-24.
"""
from __future__ import annotations

import re
from pathlib import Path

_DOCS = Path(__file__).resolve().parents[1] / "docs"
_REGISTRY = _DOCS / "audit" / "MASTER_ISSUE_REGISTRY.md"

# A number immediately describing issues, e.g. "50 issues", "53 issues total".
_COUNT = re.compile(r"\b(\d{2,3})\s+issues?\b", re.IGNORECASE)


def _markdown_files() -> list[Path]:
    return sorted(p for p in _DOCS.rglob("*.md") if p != _REGISTRY)


def test_no_document_outside_the_registry_states_a_current_issue_count() -> None:
    offenders: list[str] = []
    for path in _markdown_files():
        rel = path.relative_to(_DOCS).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _COUNT.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")

    assert not offenders, (
        "these documents state an issue count instead of pointing at the "
        "registry, which is how 50-vs-53 happened:\n  " + "\n  ".join(offenders)
    )


