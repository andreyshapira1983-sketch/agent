"""The §18 conformance ledger cannot rot silently.

`docs/audit/MEMORY_LIFECYCLE_CONTRACT.md` is on the conformance guard's
`_HISTORICAL_ANCHOR_DOCS` list, so `docs_code_conformance.py` checks that the
files it names exist but tolerates drifting line numbers. The §18 ledger's
durable references are therefore the SYMBOL NAMES, and nothing guarded them —
a rename could leave the ledger quietly citing a function that no longer
exists (Codacy raised exactly this on PR #259).

This test closes that: every code symbol the ledger names must resolve in the
tree, EXCEPT the handful the ledger deliberately names as *not built* — for
those, their absence is the point being documented.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONTRACT = REPO / "docs" / "audit" / "MEMORY_LIFECYCLE_CONTRACT.md"

# Symbols the ledger names precisely because the code does NOT have them; their
# absence is the documented fact. Keep this list tiny and each entry justified.
_NAMED_AS_ABSENT = {
    "memory_supported",   # §7.4 row: "no such verdict" — MIR-046 shipped topic-only instead
    "source_episode_id",  # §7.3 row: the CONTRACT's prescribed field name; the code links
                          # the replay by a `source_labels=["memory:<id>"]` label instead,
                          # so this field is named-but-not-built-as-named (SHIPPED in substance)
}

# Backticked tokens that are Python literals / keywords, not symbols to resolve.
_LITERALS = {"None", "True", "False"}


def _symbol_like(token: str) -> bool:
    """A backticked token that looks like a code symbol, not English/JSON prose."""
    if token in _LITERALS or token.endswith((".md", ".py")):
        return False
    base = token.split(".")[0]
    return "_" in token or "." in token or (base[:1].isupper() and base.lower() != base)


def _distinctive(token: str) -> str:
    """The most specific, greppable component of a dotted reference.

    `ProceduralMemoryStore.search` -> `ProceduralMemoryStore`;
    `memory_policy._TOKEN_RE`       -> `_TOKEN_RE`;
    `procedure_credit_allowed`      -> itself.
    """
    parts = token.split(".")
    scored = [p for p in parts if "_" in p or (p[:1].isupper() and p.lower() != p)]
    return max(scored or parts, key=len)


def _ledger_symbols() -> set[str]:
    text = CONTRACT.read_text(encoding="utf-8")
    body = text[text.index("## 18. Operational-rule"):]
    toks = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", body))
    return {t for t in toks if _symbol_like(t)}


def _resolves(symbol: str) -> bool:
    """True when the symbol occurs as a whole word in any tracked .py file."""
    result = subprocess.run(
        ["git", "grep", "-wq", "--", symbol, "--", "*.py"],
        cwd=REPO, capture_output=True,
    )
    return result.returncode == 0


def test_every_ledger_symbol_resolves_or_is_named_absent():
    missing = []
    for token in sorted(_ledger_symbols()):
        if token in _NAMED_AS_ABSENT:
            continue
        if not _resolves(_distinctive(token)):
            missing.append(token)
    assert not missing, (
        "§18 ledger names symbols that no longer exist in the code: "
        f"{missing}. Either the code was renamed (update the ledger) or the "
        "symbol was a typo. The ledger's honesty depends on this resolving."
    )


def test_symbols_named_as_absent_really_are_absent():
    """The inverse guard: if a 'not built' symbol quietly gets built, the ledger
    row saying it does not exist has itself gone stale and must be revisited."""
    still_absent = [s for s in _NAMED_AS_ABSENT if not _resolves(s)]
    assert still_absent == sorted(_NAMED_AS_ABSENT), (
        "a symbol the §18 ledger documents as NOT built now resolves in the "
        f"code: {sorted(set(_NAMED_AS_ABSENT) - set(still_absent))}. Update the "
        "ledger row — the absence it records is no longer true."
    )


def test_the_ledger_extraction_found_the_load_bearing_symbols():
    """Guards the extractor itself: if the regex silently stops matching, the
    two tests above would pass vacuously. Pin a few symbols that must be found."""
    found = _ledger_symbols()
    for anchor in ("procedure_credit_allowed", "apply_episode_feedback",
                   "admit_for_storage", "consolidate_memory"):
        assert anchor in found, f"ledger extractor missed {anchor!r}"
