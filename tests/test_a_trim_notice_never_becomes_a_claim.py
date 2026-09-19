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

from core.evidence import make_evidence
from core.evidence_budget import carries_framework_notice
from core.knowledge_pipeline import ClaimExtractor, SourceRecord

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


@pytest.mark.parametrize("text", [
    "Он замолчал... потом продолжил говорить о деле.",
    "The array a[i] is sorted before the loop begins.",
    "This is documented in the paper [4] and reproduced here.",
    "Ellipsis at the end is ordinary prose...",
    "Скобки [важное] и многоточие... порознь — обычный текст.",
    "def f(x): return x[:limit] + suffix",
])
def test_ordinary_prose_is_not_a_notice(text: str) -> None:
    assert not carries_framework_notice(text), f"false positive: {text!r}"


# ── the pipeline ─────────────────────────────────────────────────────────────

def _extract(excerpt: str):
    evidence = make_evidence(
        kind="file", source_id="file:docs/X.md", obtained_via="file_read",
        claim="Read docs/X.md", excerpt=excerpt, confidence=0.9,
    )
    source = SourceRecord(
        id=evidence.source_id, type="file", locator="docs/X.md",
        title="X", trust_level=0.95,
    )
    return ClaimExtractor().extract(evidence, source=source)


def test_no_claim_carries_a_marker_from_a_trimmed_excerpt() -> None:
    """The witness for the exact live corruption: the sentence that regenerated
    within 24 hours of the 2026-08-15 cleanup."""
    claims = _extract(
        "If a command is not here, ...[truncated]"
    )
    for claim in claims:
        assert "truncated" not in claim.text, (
            f"a trim notice became a durable claim again: {claim.text!r}"
        )


def test_clean_sentences_still_extract_beside_a_marker() -> None:
    """The gate must not swallow the document it protects: sentences the
    source actually finished still assert."""
    claims = _extract(
        "Агент не может слить ветку без решения оператора. "
        "Команды описаны в карте и проверяются на каждом прогоне сборки.\n"
        "...[INTENT-BUDGET: 120 of 9000 chars; head only]"
    )
    assert claims, "the marker gate swallowed every claim from the excerpt"
    for claim in claims:
        assert "INTENT-BUDGET" not in claim.text


# ── Audit of this closure against the field's criticism ─────────────────────
#
# From docs/audit/archive/CLOSURE_AUDIT_2026-08-22.md. Shape-based stripping is
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


def test_every_real_marker_still_survives_the_narrowing() -> None:
    """The narrowing must not open a hole: all five live writer shapes."""
    for text in _LIVE_MARKERS:
        assert carries_framework_notice(text), text


def test_the_irreducible_false_positive_is_recorded_not_hidden() -> None:
    """A one-word prose elision cannot be told from a one-word marker. Pinned
    so the limit is visible in the suite rather than only in prose — and so a
    future author who thinks they fixed it has something to turn green."""
    assert carries_framework_notice("user: ...[typing]"), (
        "if this now passes, a shape rule learned to tell prose from marker — "
        "record how, and update the audit"
    )
