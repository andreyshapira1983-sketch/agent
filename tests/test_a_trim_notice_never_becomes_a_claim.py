"""A framework trim notice never reaches a durable claim — MIR-097's repair.

WHY THIS EXISTS. Measured 2026-08-22: the live registry held 4 claims carrying
a framework truncation marker, two of them minted 2026-08-16 — ONE DAY after a
cleanup removed 20 such claims. The cleanup could not hold because the code was
untouched: `ClaimExtractor.extract` slices the POST-budget excerpt into
sentences, and the notices our own trimmers append ride along as source prose.
On 2026-08-15 that manufactured a conflict between a sentence and its own
truncated twin. The existing guard (`_is_truncated_text`) protects only the
CONFLICT detector — a corrupted excerpt could not form a conflict subject but
still became a durable claim.

THE RULE. A sentence carrying a framework notice is REFUSED, not cleaned: a
sentence interrupted by our own marker was never fully written by the source,
and half of one is not a fact. The grammar lives beside the writers in
`core/evidence_budget.py`, keyed on SHAPE (an ellipsis butted against a
bracket), because the closure criterion demands that an unseen marker of the
same class is stopped — the fix must not be fitted to `...[truncated]`. The
live corruption included `...[tr`: the budget cut the marker itself in half,
so the grammar also matches a trailing unclosed notice.

WHAT MUST SURVIVE. Ordinary prose uses both ellipses and brackets — «Он
замолчал... потом продолжил», `a[i]`, «see [4]» — and none of it butts an
ellipsis against an opening bracket. The boundary tests hold that line.
"""
from __future__ import annotations

import pytest

from core.evidence_budget import carries_framework_notice

# ── the grammar ──────────────────────────────────────────────────────────────

_LIVE_MARKERS = [
    # every family a writer in this repository produces today
    "The registry is the source of truth...[truncated]",
    "Полный список лежит в карте команд.\n...[INTENT-BUDGET: 1200 of 9000 chars; head only]",
    "Head text.\n...[7800 chars omitted]...\nTail text.",
    "Intro paragraph.\n[... 3 sections omitted ...]",
    "Intro paragraph.\n[... 2 sections omitted at end ...]",
    "Body.\n...[TOTAL-BUDGET: trimmed to 500 of 9000 chars to fit 12000-char total evidence budget]",
    # measured live 2026-08-04: the budget cut the marker itself mid-word
    "several have since been fixed with regression te...[tr",
]


@pytest.mark.parametrize("text", _LIVE_MARKERS)
def test_every_live_marker_family_is_recognised(text: str) -> None:
    assert carries_framework_notice(text), f"not recognised: {text!r}"


def test_an_unseen_marker_of_the_same_shape_is_recognised() -> None:
    """The closure criterion's own demand: a notice no current writer emits —
    same grammar, different vocabulary — is still caught, so the repair is not
    a blacklist of today's strings."""
    for unseen in (
        "The build is green...[SELF-DOC NOTE: 3 of 9 notes shown]",
        "Начало файла...[PER-FILE TRIM: хвост опущен]",
        "Fine so far…[CACHE: stale copy]",
    ):
        assert carries_framework_notice(unseen), f"unseen form passed: {unseen!r}"


# ── Audit of this closure against the field's criticism ─────────────────────
#
# From CLOSURE_AUDIT_2026-08-22.md in git history. Shape-based stripping is
# criticised for eating legitimate text, and tested against real prose the
# criticism LANDED: a bibliographic «...[1998]» and a quotation elision
# «...[и]» were both refused. Both are now exempt — bracket content that is a
# bare number or a single character is never one of our markers.
#
# What is NOT fixed, and is recorded rather than argued away: «user:
# ...[typing]» is structurally identical to «...[truncated]» — one word in
# brackets after an ellipsis — and no shape rule can separate them. The cost
# of that false positive is a REFUSED claim, never a corrupted fact, so the
# residue is left in the safe direction.

def test_bibliographic_and_elision_brackets_are_not_notices() -> None:
    for text in (
        "Smith et al. ...[1998] showed the effect",
        "«...[и] дальше по тексту» — обычная цитата",
        "the passage reads ...[а] and continues",
    ):
        assert not carries_framework_notice(text), text
