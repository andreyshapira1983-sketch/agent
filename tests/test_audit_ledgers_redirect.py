"""Guard: the historical audit docs redirect status to the single registry.

CORE_AUDIT, OPERATIONAL_FAILURE_MODES, MEMORY_SYSTEM_AUDIT and LIVE_PROBE_FINDINGS
each carry their own (now historical) status ledger. To keep those stale
statuses from being read as current, each must carry a **status-ledger-superseded
banner** — a Markdown blockquote that both says it is superseded and links to the
single status owner, docs/audit/MASTER_ISSUE_REGISTRY.md.

The contract is asserted *structurally* (both terms inside the same blockquote
block), not as independent document-wide substring checks: several of these docs
use the word "superseded" in unrelated content (e.g. memory-lifecycle fields),
so a loose check would false-pass even after the banner was deleted. Read-only.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HISTORICAL_AUDIT_DOCS = (
    "docs/CORE_AUDIT_2026-07-18.md",
    "docs/OPERATIONAL_FAILURE_MODES.md",
    "docs/MEMORY_SYSTEM_AUDIT.md",
    "docs/LIVE_PROBE_FINDINGS.md",
)

# The exact relative link target every banner must point at (from docs/*.md).
REGISTRY_LINK = "](audit/MASTER_ISSUE_REGISTRY.md)"


def _blockquote_blocks(text: str) -> list[str]:
    """Return each run of consecutive Markdown blockquote (`>`) lines as a block."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def has_superseded_registry_banner(text: str) -> bool:
    """True iff one blockquote block both says 'superseded' and links the registry."""
    for block in _blockquote_blocks(text):
        if "superseded" in block.lower() and REGISTRY_LINK in block:
            return True
    return False


def test_each_doc_has_superseded_registry_banner():
    missing = [
        rel
        for rel in HISTORICAL_AUDIT_DOCS
        if not has_superseded_registry_banner(
            (REPO_ROOT / rel).read_text(encoding="utf-8")
        )
    ]
    assert not missing, (
        "these historical audit docs lack a status-ledger-superseded banner that "
        f"links to the registry within one blockquote: {missing}. Add a banner "
        "saying the ledger is superseded and linking "
        "[`audit/MASTER_ISSUE_REGISTRY.md`](audit/MASTER_ISSUE_REGISTRY.md)."
    )


def test_contract_requires_terms_in_the_same_block():
    # Scattered terms (superseded in one block, the link in unrelated prose) must
    # NOT satisfy the contract — this is the false-green the loose check allowed.
    scattered = (
        "> a note that something is superseded\n"
        "\n"
        "See [registry](audit/MASTER_ISSUE_REGISTRY.md) elsewhere in prose.\n"
    )
    assert not has_superseded_registry_banner(scattered)

    # A real banner: both terms in the same blockquote block.
    banner = (
        "> **SUPERSEDED (2026-07-25).** statuses live only in\n"
        "> [`audit/MASTER_ISSUE_REGISTRY.md`](audit/MASTER_ISSUE_REGISTRY.md).\n"
    )
    assert has_superseded_registry_banner(banner)
