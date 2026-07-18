"""Low-evidence answer policy.

Empirical observation from live runs: when the verifier produces a low
``evidence_score`` (e.g. 0.0 with ``verified=6, unverified=33``), the
agent still emits a long, polished answer — a 30-day product launch
plan, a market report, a step-by-step business strategy. The
:mod:`core.confidence_gate` already detects this as
``low_confidence_gate``, but it is purely observational: the long,
under-supported answer ships unchanged.

This module is the *enforcement* layer paired with the gate. When
the verifier's verdict distribution is *severely* below threshold, the
final answer is rewritten to a short, honest reply that:

  * keeps only the chunks the verifier marked ``verified``;
  * states explicitly that evidence was insufficient (in the user's
    locale, EN or RU);
  * downgrades the Output Contract ``Confidence`` line to ``low``;
  * routes the suppressed bulk into the ``Unverified`` section so
    the count is visible to the operator.

The policy fires *strictly* — its trigger is more conservative than the
informational :class:`ConfidenceGate` so it cannot quietly truncate
borderline answers.

Trigger contract::

    triggered iff
        report.total_chunks >= MIN_TOTAL
      and verified_ratio = verified / total <= MAX_VERIFIED_RATIO
      and (
            verified == 0
         or unverified + cited_unmatched + topic_supported >= UNVERIFIED_FLOOR
      )

The last clause is a safety net: a 5-chunk answer with 1 verified +
4 unverified would otherwise pass the ratio gate accidentally because
the small denominator leaves the agent room to be wrong about most
claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_MIN_TOTAL = 8
_DEFAULT_MAX_VERIFIED_RATIO = 0.20
_DEFAULT_UNVERIFIED_FLOOR = 6


# Roles whose deliverable is NEW synthesized code / docstring / diff rather than
# factual claims about the world. Such output can never appear verbatim in the
# source file it was derived from, so the factual verifier marks it "unverified"
# and the low-evidence gate would delete exactly what the user asked to create.
# For these roles the synthesis IS the deliverable — evidence is not expected.
_GENERATIVE_ROLES: frozenset[str] = frozenset({"programmer"})


def is_evidence_expected(
    *,
    role: str = "",
    chain_was_empty: bool = False,
    realtime_required: bool = True,
) -> bool:
    """Whether the low-evidence truncation gate should apply to this turn.

    Evidence is NOT expected (gate disabled) when either:

      * the deliverable is generative code (``role`` in ``_GENERATIVE_ROLES``) —
        a docstring/diff/code turn that read a file as *input* still produces new
        text that cannot be verbatim-verified against that file; or
      * the evidence chain is empty AND the question carries no realtime intent
        (a pure reasoning/design answer where the model's synthesis is the whole
        deliverable and there is nothing to cite).

    Factual / realtime turns (researcher, technical_report, operator_chat, …)
    keep the full gate, so unsupported factual answers are still suppressed.
    """
    if role in _GENERATIVE_ROLES:
        return False
    return not (chain_was_empty and not realtime_required)


@dataclass(frozen=True)
class LowEvidencePolicyResult:
    """Outcome of one evaluation."""

    triggered: bool
    answer: str
    verified_chunks: int
    total_chunks: int
    verified_ratio: float
    unverified_total: int
    reason: str = ""
    locale: str = "en"
    suppressed_chars: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "verified_chunks": self.verified_chunks,
            "total_chunks": self.total_chunks,
            "verified_ratio": round(self.verified_ratio, 3),
            "unverified_total": self.unverified_total,
            "reason": self.reason,
            "locale": self.locale,
            "suppressed_chars": self.suppressed_chars,
        }


def _looks_russian(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def _insufficient_data_notice(locale: str) -> str:
    if locale == "ru":
        return (
            "Недостаточно данных для развёрнутого ответа: проверенных "
            "источников слишком мало. Полный план/анализ скрыт, чтобы "
            "не выдавать предположения за факты."
        )
    return (
        "Insufficient evidence to ship a full answer: too few claims "
        "could be backed by the gathered sources. The long planned "
        "response was suppressed to avoid presenting guesses as facts."
    )


def _conclusion_stub(verified_count: int, locale: str) -> str:
    if locale == "ru":
        if verified_count == 0:
            return (
                "Conclusion: ни одно утверждение не подтверждено "
                "источниками этого цикла."
            )
        return (
            f"Conclusion: подтверждено {verified_count} утверждений; "
            "остальное скрыто как недостаточно обоснованное."
        )
    if verified_count == 0:
        return (
            "Conclusion: no claim could be backed by the sources "
            "gathered this cycle."
        )
    return (
        f"Conclusion: {verified_count} claim(s) verified; the rest of "
        "the planned reply was suppressed for lack of support."
    )


def _facts_block(verified_chunks: list[str], locale: str) -> str:
    if not verified_chunks:
        if locale == "ru":
            return "Facts: (нет утверждений, прошедших проверку)"
        return "Facts: (no verified claim from the suppressed draft)"
    body = "\n".join(f"  - {chunk}" for chunk in verified_chunks)
    return f"Facts:\n{body}"


def _unverified_block(
    suppressed_count: int, notice: str, locale: str
) -> str:
    if locale == "ru":
        return (
            f"Unverified: подавлено {suppressed_count} непроверенных "
            f"утверждений из исходного черновика. {notice}"
        )
    return (
        f"Unverified: {suppressed_count} unverified claim(s) suppressed "
        f"from the original draft. {notice}"
    )


def _build_short_answer(
    *,
    verified_claim_texts: list[str],
    suppressed_count: int,
    locale: str,
) -> str:
    """Assemble a deterministic short reply that follows the Output
    Contract section order. We rebuild from scratch — never paraphrase
    the LLM's prose — so the truncation is visibly mechanical."""
    notice = _insufficient_data_notice(locale)
    parts = [
        _conclusion_stub(len(verified_claim_texts), locale),
        _facts_block(verified_claim_texts, locale),
        "Sources: only verified claims listed above (if any)",
        "Confidence: low",
        _unverified_block(suppressed_count, notice, locale),
        "Safety: ok",
    ]
    return "\n\n".join(parts)


# Verdict labels routed to "needs evidence support" buckets. Mirrors
# the strings produced by core/verifier.py so a downstream change there
# raises a single import-test failure if the names drift.
_UNSUPPORTED_VERDICTS: frozenset[str] = frozenset({
    "unverified",
    "cited_but_unmatched",
    "topic_supported_but_claim_unverified",
    "subagent_asserted",
})


def evaluate_low_evidence_policy(
    *,
    answer: str,
    report: Any,
    question: str = "",
    min_total_chunks: int = _DEFAULT_MIN_TOTAL,
    max_verified_ratio: float = _DEFAULT_MAX_VERIFIED_RATIO,
    unverified_floor: int = _DEFAULT_UNVERIFIED_FLOOR,
    evidence_expected: bool = True,
    local_critique_active: bool = False,
) -> LowEvidencePolicyResult:
    """Decide whether to truncate the answer because evidence is too thin.

    The decision is purely a function of the verification distribution +
    a length floor; no LLM call. When triggered, the result carries a
    rebuilt short answer that follows the Output Contract.

    Locale (EN / RU) is detected from the user's question first, then
    falls back to the answer text. RU users get RU notices; everyone
    else gets EN.

    When ``local_critique_active`` is true (resolved referent critique
    path), empty rewrite is forbidden even if the bulk trigger would
    fire — the analysis target is known (critique plan PR3 invariant).
    """
    if report is None:
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=0, total_chunks=0,
            verified_ratio=0.0, unverified_total=0,
            reason="no_report",
        )

    total = int(getattr(report, "total_chunks", 0) or 0)
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    unverified = int(getattr(report, "unverified_chunks", 0) or 0)
    cited_unmatched = int(
        getattr(report, "cited_but_unmatched_chunks", 0) or 0
    )
    topic_supported = int(
        getattr(
            report, "topic_supported_but_claim_unverified_chunks", 0
        ) or 0
    )
    subagent_asserted = int(
        getattr(report, "subagent_asserted_chunks", 0) or 0
    )
    unverified_total = (
        unverified + cited_unmatched + topic_supported + subagent_asserted
    )
    verified_ratio = (verified / total) if total > 0 else 0.0

    if local_critique_active:
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=verified_ratio,
            unverified_total=unverified_total,
            reason="local_critique_skip_empty_rewrite",
        )

    if not evidence_expected:
        # The task never needed external evidence (pure reasoning / design
        # question with an empty evidence chain and no realtime intent).
        # Truncating here would suppress a legitimate answer just because it
        # has nothing to cite — the model's synthesis IS the deliverable.
        # Factual / realtime questions keep the full gate (evidence_expected
        # stays True for them).
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=verified_ratio,
            unverified_total=unverified_total,
            reason="no_evidence_expected",
        )

    if total < min_total_chunks:
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=verified_ratio,
            unverified_total=unverified_total,
            reason="too_few_chunks_to_truncate",
        )

    if verified_ratio > max_verified_ratio:
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=verified_ratio,
            unverified_total=unverified_total,
            reason="verified_ratio_above_threshold",
        )

    if verified > 0 and unverified_total < unverified_floor:
        # Borderline: verified > 0 but unverified mass too small to
        # justify the truncation hammer. The standard ConfidenceGate
        # log will still surface it.
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=verified_ratio,
            unverified_total=unverified_total,
            reason="unverified_mass_below_floor",
        )

    locale = "ru" if (
        _looks_russian(question) or _looks_russian(answer)
    ) else "en"

    chunks_iter = getattr(report, "chunks", ()) or ()
    verified_texts: list[str] = []
    for c in chunks_iter:
        verdict = getattr(c, "verdict", "")
        text = getattr(c, "text", "")
        if verdict == "verified" and text.strip():
            verified_texts.append(text.strip())

    suppressed_count = total - verified
    short_answer = _build_short_answer(
        verified_claim_texts=verified_texts,
        suppressed_count=suppressed_count,
        locale=locale,
    )
    reason = (
        f"verified_ratio={verified_ratio:.2f} <= {max_verified_ratio} "
        f"and unverified_total={unverified_total} >= {unverified_floor}"
    )
    return LowEvidencePolicyResult(
        triggered=True,
        answer=short_answer,
        verified_chunks=verified,
        total_chunks=total,
        verified_ratio=verified_ratio,
        unverified_total=unverified_total,
        reason=reason,
        locale=locale,
        suppressed_chars=max(0, len(answer) - len(short_answer)),
    )
