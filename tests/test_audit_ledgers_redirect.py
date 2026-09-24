"""Guard: the historical audit docs redirect status to the single registry.

MEMORY_SYSTEM_AUDIT and LIVE_PROBE_FINDINGS each carry their own (now historical) status ledger. To keep those stale
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
    "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
    "docs/LIVE_PROBE_FINDINGS.md",
)

# The exact relative link target every banner must point at (from docs/*.md).
REGISTRY_NAME = "MASTER_ISSUE_REGISTRY.md"
# Kept for the unit test below: the strictest banner form is a real relative
# link. The per-document contract accepts the registry NAME inside the same
# blockquote (the live banners cite it as an inline-code path, and the two
# documents sit at different depths, so no single relative link fits all).
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
    """True iff one blockquote block both says 'superseded' and names the registry."""
    for block in _blockquote_blocks(text):
        if "superseded" in block.lower() and REGISTRY_NAME in block:
            return True
    return False


def test_every_historical_audit_doc_carries_the_banner():
    """The guard this file existed for — and, found 2026-09-03, never ran: the
    doc list and the helper were defined, but no test walked the documents. A
    dead guard is the registered-but-not-operational defect in miniature."""
    for rel in HISTORICAL_AUDIT_DOCS:
        path = REPO_ROOT / rel
        assert path.is_file(), f"{rel}: the listed document is gone — update the list"
        text = path.read_text(encoding="utf-8")
        assert has_superseded_registry_banner(text), (
            f"{rel}: no blockquote block says 'superseded' AND names "
            f"{REGISTRY_NAME} — its stale statuses can be read as current"
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
