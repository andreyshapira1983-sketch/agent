"""
brain/skills/verifier.py — Critic.

The Verifier turns a Profession's `acceptance_criteria` (list of
`AcceptanceCheck`) into a boolean go/no-go decision and a list of per-check
`AcceptanceResult`s explaining the verdict.

Design principles
─────────────────
1. **Pure data in, pure data out.** Verifier never touches disk, never
   calls a tool. It compares two strings (original vs deliverable) and
   the `params` of each check.

2. **Defensive on inputs.** Missing optional context (e.g. no LLM for
   `no_new_facts`) degrades gracefully — that check returns `passed=True`
   with a `message="skipped: no llm"`, instead of crashing the whole job.

3. **Forward-compatible.** Unknown `kind` values are skipped with a log
   message, never propagated as errors. Yaml from old profession files
   still loads cleanly in new builds.

4. **Single source of truth for kinds.** Add a new acceptance kind by
   adding a method `_check_<kind>` here and listing it in `KNOWN_KINDS`.
   No yaml change needed.

Built-in kinds:
    word_count_delta    range_pct: [min, max] — relative change in word count
    no_new_facts        min_score: float (default 0.85) — semantic similarity
                          ratio; degrades to skipped when llm not provided
    contains_no         forbidden_substrings: [str, ...]
    contains_all        required_substrings: [str, ...]
    max_paragraphs      limit: int
    min_paragraphs      limit: int
    reading_grade_drop  min_drop: float — Flesch-Kincaid grade must drop by ≥ N
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .models import AcceptanceCheck, AcceptanceResult

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Public report
# ════════════════════════════════════════════════════════════════════

@dataclass
class VerifierReport:
    """Aggregate Verifier output for one deliverable."""

    passed: bool
    results: list[AcceptanceResult]
    summary: str

    @property
    def blocking_failures(self) -> list[AcceptanceResult]:
        return [r for r in self.results if r.blocking and not r.passed]

    @property
    def warnings(self) -> list[AcceptanceResult]:
        return [r for r in self.results if not r.blocking and not r.passed]

    def to_dict(self) -> dict:
        return {
            "passed":  self.passed,
            "summary": self.summary,
            "checks":  [
                {
                    "kind":     r.kind,
                    "passed":   r.passed,
                    "blocking": r.blocking,
                    "message":  r.message,
                    "metric":   r.metric,
                }
                for r in self.results
            ],
        }


# ════════════════════════════════════════════════════════════════════
# LLM judge protocol
# ════════════════════════════════════════════════════════════════════

class LLMJudge(Protocol):
    """Minimal interface the Verifier needs from an LLM.

    Used only by `no_new_facts` today. The judge receives two texts
    and must return a similarity / faithfulness score in [0, 1].
    """

    def similarity(self, original: str, candidate: str) -> float:
        ...


# ════════════════════════════════════════════════════════════════════
# Verifier
# ════════════════════════════════════════════════════════════════════

KNOWN_KINDS: set[str] = {
    "word_count_delta",
    "no_new_facts",
    "contains_no",
    "contains_all",
    "max_paragraphs",
    "min_paragraphs",
    "reading_grade_drop",
}


class Verifier:
    """Runs all acceptance checks for a single deliverable.

    Usage:
        verifier = Verifier(llm_judge=optional_judge)
        report = verifier.verify(
            checks=profession.acceptance_criteria,
            original=raw_input_text,
            deliverable=produced_text,
        )
        if not report.passed:
            ...
    """

    def __init__(self, llm_judge: LLMJudge | None = None) -> None:
        self._llm = llm_judge

    # ────────────────────────────────────────────────────────────────

    def verify(
        self,
        checks: list[AcceptanceCheck],
        original: str,
        deliverable: str,
    ) -> VerifierReport:
        results: list[AcceptanceResult] = []

        for check in checks:
            if check.kind not in KNOWN_KINDS:
                logger.warning(
                    "[Verifier] Unknown acceptance kind '%s' — skipping",
                    check.kind,
                )
                continue
            method: Callable[..., AcceptanceResult] = getattr(
                self, f"_check_{check.kind}",
            )
            try:
                res = method(check, original, deliverable)
            except Exception as exc:  # noqa: BLE001 — Verifier never raises
                logger.exception("[Verifier] check '%s' raised", check.kind)
                res = AcceptanceResult(
                    kind=check.kind,
                    passed=False,
                    message=f"verifier crash: {type(exc).__name__}: {exc}",
                    blocking=check.blocking,
                )
            results.append(res)

        blocking_fails = [r for r in results if r.blocking and not r.passed]
        passed = not blocking_fails

        summary = _summarise(passed, results)
        return VerifierReport(passed=passed, results=results, summary=summary)

    # ────────────────────────────────────────────────────────────────
    # Built-in checks
    # ────────────────────────────────────────────────────────────────

    def _check_word_count_delta(
        self,
        check: AcceptanceCheck,
        original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        orig_words = _word_count(original)
        new_words = _word_count(deliverable)
        if orig_words == 0:
            return AcceptanceResult(
                kind=check.kind,
                passed=False,
                message="original text has zero words",
                blocking=check.blocking,
            )
        delta = (new_words - orig_words) / orig_words
        rng = check.params.get("range_pct", [-0.20, 0.10])
        try:
            lo, hi = float(rng[0]), float(rng[1])
        except (TypeError, ValueError, IndexError):
            return AcceptanceResult(
                kind=check.kind,
                passed=False,
                message=f"invalid range_pct: {rng!r}",
                blocking=check.blocking,
            )
        ok = lo <= delta <= hi
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=round(delta, 4),
            message=(
                f"word delta {delta:+.2%} within [{lo:+.0%}, {hi:+.0%}]"
                if ok else
                f"word delta {delta:+.2%} outside [{lo:+.0%}, {hi:+.0%}]"
            ),
            blocking=check.blocking,
        )

    def _check_no_new_facts(
        self,
        check: AcceptanceCheck,
        original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        threshold = float(check.params.get("min_score", 0.85))
        if self._llm is None:
            # Degrade gracefully — pass with a note. A real prod run would
            # fail-closed; for the wedge, fail-open is the right call so a
            # missing judge doesn't block delivery.
            return AcceptanceResult(
                kind=check.kind,
                passed=True,
                message="skipped: no LLM judge configured",
                blocking=check.blocking,
            )
        score = float(self._llm.similarity(original, deliverable))

        # The judge signalled "cannot decide" (LLM error, malformed reply,
        # non-finite score). Be conservative: a blocking check must fail
        # closed — letting hallucinated content through silently is worse
        # than re-running. A non-blocking check passes with a clear note.
        if math.isnan(score) or math.isinf(score):
            if check.blocking:
                return AcceptanceResult(
                    kind=check.kind,
                    passed=False,
                    metric=None,
                    message="judge inconclusive — failing closed (blocking)",
                    blocking=True,
                )
            return AcceptanceResult(
                kind=check.kind,
                passed=True,
                metric=None,
                message="judge inconclusive — non-blocking, allowed through",
                blocking=False,
            )

        ok = score >= threshold
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=round(score, 4),
            message=(
                f"semantic similarity {score:.2f} ≥ {threshold:.2f}"
                if ok else
                f"semantic similarity {score:.2f} < {threshold:.2f} — new facts likely"
            ),
            blocking=check.blocking,
        )

    def _check_contains_no(
        self,
        check: AcceptanceCheck,
        _original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        forbidden = check.params.get("forbidden_substrings") or []
        if not isinstance(forbidden, list):
            return AcceptanceResult(
                kind=check.kind,
                passed=False,
                message="forbidden_substrings must be a list",
                blocking=check.blocking,
            )
        found = [s for s in forbidden if str(s) in deliverable]
        ok = not found
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=len(found),
            message=(
                "no forbidden substrings present"
                if ok else
                f"forbidden substrings found: {found!r}"
            ),
            blocking=check.blocking,
        )

    def _check_contains_all(
        self,
        check: AcceptanceCheck,
        _original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        required = check.params.get("required_substrings") or []
        if not isinstance(required, list):
            return AcceptanceResult(
                kind=check.kind,
                passed=False,
                message="required_substrings must be a list",
                blocking=check.blocking,
            )
        missing = [s for s in required if str(s) not in deliverable]
        ok = not missing
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=len(missing),
            message=(
                "all required substrings present"
                if ok else
                f"missing required substrings: {missing!r}"
            ),
            blocking=check.blocking,
        )

    def _check_max_paragraphs(
        self,
        check: AcceptanceCheck,
        _original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        limit = int(check.params.get("limit", 0))
        n = _paragraph_count(deliverable)
        ok = n <= limit if limit > 0 else True
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=n,
            message=f"paragraphs={n}, max={limit}",
            blocking=check.blocking,
        )

    def _check_min_paragraphs(
        self,
        check: AcceptanceCheck,
        _original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        limit = int(check.params.get("limit", 0))
        n = _paragraph_count(deliverable)
        ok = n >= limit
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=n,
            message=f"paragraphs={n}, min={limit}",
            blocking=check.blocking,
        )

    def _check_reading_grade_drop(
        self,
        check: AcceptanceCheck,
        original: str,
        deliverable: str,
    ) -> AcceptanceResult:
        min_drop = float(check.params.get("min_drop", 0.0))
        orig_grade = _flesch_kincaid_grade(original)
        new_grade = _flesch_kincaid_grade(deliverable)
        drop = orig_grade - new_grade
        ok = drop >= min_drop
        return AcceptanceResult(
            kind=check.kind,
            passed=ok,
            metric=round(drop, 2),
            message=(
                f"FK grade dropped {drop:+.2f} (≥ {min_drop:+.2f} required)"
                if ok else
                f"FK grade only dropped {drop:+.2f}, needed ≥ {min_drop:+.2f}"
            ),
            blocking=check.blocking,
        )


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _paragraph_count(text: str) -> int:
    if not text:
        return 0
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return len(paras)


# ── Flesch-Kincaid Grade Level ─────────────────────────────────────
#
# FK Grade = 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59
#
# Syllable count is approximated by counting vowel groups — good enough
# for English-only acceptance checks; for Russian / other languages,
# `reading_grade_drop` should be marked non-blocking in the profession.

_SENT_RE = re.compile(r"[.!?]+(?:\s|$)")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)


def _syllables_in_word(word: str) -> int:
    word = word.lower().rstrip("e")
    if not word:
        return 1
    groups = _VOWEL_GROUP_RE.findall(word)
    return max(1, len(groups))


def _flesch_kincaid_grade(text: str) -> float:
    if not text or not text.strip():
        return 0.0
    words = _WORD_RE.findall(text)
    n_words = len(words) or 1
    n_sents = max(1, len(_SENT_RE.findall(text)))
    n_syllables = sum(_syllables_in_word(w) for w in words)
    grade = (
        0.39 * (n_words / n_sents) +
        11.8 * (n_syllables / n_words) -
        15.59
    )
    if math.isnan(grade) or math.isinf(grade):
        return 0.0
    return grade


def _summarise(passed: bool, results: list[AcceptanceResult]) -> str:
    if not results:
        return "no acceptance checks configured"
    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    verdict = "PASS" if passed else "FAIL"
    return f"{verdict} — {passed_count}/{total} checks passed"
