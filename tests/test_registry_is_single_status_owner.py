"""No document outside the registry may state an issue count.

`docs/INDEX.md` names MASTER_ISSUE_REGISTRY the single owner of issue status.
That only holds if other documents point AT it instead of copying from it —
copies do not get updated, and the copy is what gets quoted.

This is not hypothetical. Before this guard: the progress tracker's "CURRENT
STATE" banner said 50 while the registry held 53, and the lifecycle contract
repeated 50 in its provenance line. Both were written as authoritative.

Chronological log entries are exempt and deliberately so — "Registry now 42
issues" is a true statement about a past moment and part of how the revision
is reconstructed. The rule is about claims of CURRENT state, so the exemption
is narrow and explicit: a line inside a log section, or one this file lists by
hand with a reason.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[1] / "docs"
_REGISTRY = _DOCS / "audit" / "MASTER_ISSUE_REGISTRY.md"

# A number immediately describing issues, e.g. "50 issues", "53 issues total".
_COUNT = re.compile(r"\b(\d{2,3})\s+issues?\b", re.I)

# Lines allowed to carry a historical count, with the reason they are allowed.
_ALLOWED = {
    # The progress tracker is a chronological log; these record what was true
    # at each stage and are explicitly framed as history by its banner.
    "audit/AUDIT_PROGRESS.md": "chronological log of the revision",
    # INDEX explains the drift problem itself and quotes an old figure as the
    # example of what went wrong.
    "INDEX.md": "documents the drift problem, quoting a stale figure as evidence",
}


def _markdown_files() -> list[Path]:
    return sorted(p for p in _DOCS.rglob("*.md") if p != _REGISTRY)


def test_no_document_outside_the_registry_states_a_current_issue_count() -> None:
    offenders: list[str] = []
    for path in _markdown_files():
        rel = path.relative_to(_DOCS).as_posix()
        if rel in _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _COUNT.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")

    assert not offenders, (
        "these documents state an issue count instead of pointing at the "
        "registry, which is how 50-vs-53 happened:\n  " + "\n  ".join(offenders)
    )


def test_the_registry_itself_does_not_hand_type_a_total() -> None:
    """The registry's own total must come from the generator, not prose."""
    text = _REGISTRY.read_text(encoding="utf-8")
    generated = [l for l in text.splitlines() if "**TOTAL**" in l]

    assert generated, "the generated tally row is missing"
    assert "registry_tally.py" in generated[0], (
        "the total must be attributed to its generator so a reader knows not "
        "to edit it by hand"
    )


@pytest.mark.parametrize("rel,reason", sorted(_ALLOWED.items()))
def test_exemptions_stay_justified(rel: str, reason: str) -> None:
    """An exemption with no counts left should be removed, not left standing."""
    path = _DOCS / rel
    assert path.exists(), f"exempted file {rel} no longer exists"
    assert any(_COUNT.search(l) for l in path.read_text(encoding="utf-8").splitlines()), (
        f"{rel} no longer states any count — drop its exemption ({reason})"
    )
