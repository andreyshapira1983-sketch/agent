"""Low-evidence answer policy.

Empirical observation from live runs: when the verifier produces a low
``evidence_score`` (e.g. 0.0 with ``verified=6, unverified=33``), the
agent still emits a long, polished answer — a 30-day product launch
plan, a market report, a step-by-step business strategy. The
:mod:`core.evidence_support` already reports this as
``evidence_support``, but it is purely observational: the long,
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
informational evidence-support telemetry so it cannot quietly truncate
borderline answers.

Trigger contract::

    supported = verified + dialogue_supported

    triggered iff
        report.total_chunks >= MIN_TOTAL
      and supported_ratio = supported / total <= MAX_VERIFIED_RATIO
      and (
            supported == 0
         or unverified + cited_unmatched + topic_supported >= UNVERIFIED_FLOOR
      )

The last clause is a safety net: a 5-chunk answer with 1 verified +
4 unverified would otherwise pass the ratio gate accidentally because
the small denominator leaves the agent room to be wrong about most
claims.

``dialogue_supported`` joins the numerator because of issue #119: a
claim about *this session's own exchange* is backed by the verbatim
transcript, so counting it as unsupported mass is what let the gate
delete a valid self-correction. It is support, not verification — it
is never folded into ``verified_chunks``, and a world claim never
earns it (the scoping test lives in :mod:`core.evidence_classes`).
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

#: Markers of an actually generated artifact in the answer text. A fenced block
#: or a unified-diff header is newly synthesized output; ordinary prose about a
#: repository is not, however programmer-ish the role that produced it.
_FENCE_MARKER = "```"
_DIFF_MARKERS: tuple[str, ...] = ("--- ", "+++ ", "@@ ")


def _carries_generated_artifact(answer: str) -> bool:
    """Whether *answer* actually contains generated code / a diff.

    Deterministic and regex-free, matching the rest of this module. A fenced
    block anywhere counts; diff markers count only at the start of a line, so
    a sentence containing "---" in prose does not qualify.
    """

    if not answer:
        return False
    if _FENCE_MARKER in answer:
        return True
    return any(
        line.startswith(_DIFF_MARKERS)
        for line in answer.splitlines()
    )


def is_evidence_expected(
    *,
    role: str = "",
    chain_was_empty: bool = False,
    realtime_required: bool = True,
    answer: str | None = None,
) -> bool:
    """Whether the low-evidence truncation gate should apply to this turn.

    Evidence is NOT expected (gate disabled) when either:

      * the deliverable is generative code (``role`` in ``_GENERATIVE_ROLES``)
        **and the answer actually carries a generated artifact** — a
        docstring/diff/code turn that read a file as *input* still produces new
        text that cannot be verbatim-verified against that file; or
      * the evidence chain is empty AND the question carries no realtime intent
        (a pure reasoning/design answer where the model's synthesis is the whole
        deliverable and there is nothing to cite).

    Factual / realtime turns (researcher, technical_report, operator_chat, …)
    keep the full gate, so unsupported factual answers are still suppressed.

    The role exemption used to key on WHO answered rather than WHAT was
    produced. Measured live: "In one sentence: what does core/model_router.py
    do?" read the file, produced 7 chunks of which 6 verified, and still logged
    ``no_evidence_expected`` — because it ran under ``role=programmer``. The
    same class was already recorded in ``docs/COGNITIVE_CORE.md`` (open
    question 3) for "how many TODO/FIXME are in core/?". Counting and
    describing are not code generation, and the exemption's own rationale —
    generated text "can never appear verbatim in the source file" — reaches
    exactly as far as answers that carry a generated artifact.

    ``answer`` is optional: a caller that does not hand over the text gets the
    previous role-only behaviour, so no existing call site changes silently.
    """
    if role in _GENERATIVE_ROLES:
        if answer is None:
            return False
        return not _carries_generated_artifact(answer)
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
    dialogue_supported_chunks: int = 0
    #: Never part of `supported_chunks` (operator ruling 2026-08-03, MIR-028):
    #: the user's words prove the words, not the world.
    user_asserted_chunks: int = 0

    @property
    def supported_chunks(self) -> int:
        """Claims the answer is entitled to keep: verified + dialogue-scoped."""
        return self.verified_chunks + self.dialogue_supported_chunks

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "verified_chunks": self.verified_chunks,
            "dialogue_supported_chunks": self.dialogue_supported_chunks,
            "user_asserted_chunks": self.user_asserted_chunks,
            "supported_chunks": self.supported_chunks,
            "total_chunks": self.total_chunks,
            # `verified_ratio` is kept for existing log consumers, but the value
            # is and always was the ratio the gate DECIDES on — since issue #119
            # that is (verified + dialogue_supported) / total. `supported_ratio`
            # is the name that says so; prefer it in new consumers.
            "verified_ratio": round(self.verified_ratio, 3),
            "supported_ratio": round(self.verified_ratio, 3),
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


def _conclusion_stub(
    verified_count: int, locale: str, dialogue_count: int = 0
) -> str:
    """Conclusion line for the rebuilt short answer.

    ``dialogue_count`` is stated separately, never merged into the verified
    tally: claims about this session's own exchange are supported by the
    transcript, not confirmed by a source (issue #119).
    """
    if locale == "ru":
        if verified_count == 0 and dialogue_count == 0:
            return (
                "Conclusion: ни одно утверждение не подтверждено "
                "источниками этого цикла."
            )
        if verified_count == 0:
            return (
                "Conclusion: внешние источники не подтвердили ничего; "
                f"сохранено {dialogue_count} утверждений о самом диалоге "
                "(по стенограмме сессии)."
            )
        tail = (
            f" плюс {dialogue_count} о самом диалоге;"
            if dialogue_count
            else ";"
        )
        return (
            f"Conclusion: подтверждено {verified_count} утверждений{tail} "
            "остальное скрыто как недостаточно обоснованное."
        )
    if verified_count == 0 and dialogue_count == 0:
        return (
            "Conclusion: no claim could be backed by the sources "
            "gathered this cycle."
        )
    if verified_count == 0:
        return (
            "Conclusion: no external source confirmed anything; "
            f"{dialogue_count} claim(s) about this session's own exchange "
            "were kept (backed by the transcript)."
        )
    tail = (
        f", plus {dialogue_count} about this session's own exchange;"
        if dialogue_count
        else ";"
    )
    return (
        f"Conclusion: {verified_count} claim(s) verified{tail} the rest of "
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
    dialogue_count: int = 0,
) -> str:
    """Assemble a deterministic short reply that follows the Output
    Contract section order. We rebuild from scratch — never paraphrase
    the LLM's prose — so the truncation is visibly mechanical."""
    notice = _insufficient_data_notice(locale)
    parts = [
        _conclusion_stub(
            len(verified_claim_texts) - dialogue_count, locale, dialogue_count
        ),
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
    dialogue = int(getattr(report, "dialogue_supported_chunks", 0) or 0)
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
    # `user_asserted` (operator ruling 2026-08-03, MIR-028) joins neither
    # `supported` (the user's words never verify their own content, so an
    # all-echo answer must not score supported_ratio=1.0 and slip past this
    # gate) nor `unverified_total` (nothing was fabricated, so it does not
    # push the floor). It still sits in `total_chunks`, i.e. it DOES drag
    # supported_ratio down — "neutral" means neutral between those two
    # counters, not absent from the trigger math. Its main weight lands in
    # episode banking (`weak_chunks`) and the evidence-support score.
    user_asserted = int(getattr(report, "user_asserted_chunks", 0) or 0)
    unverified_total = (
        unverified + cited_unmatched + topic_supported + subagent_asserted
    )
    supported = verified + dialogue
    supported_ratio = (supported / total) if total > 0 else 0.0

    def _result(*, triggered: bool, answer_out: str, reason: str,
                locale: str = "en", suppressed_chars: int = 0
                ) -> LowEvidencePolicyResult:
        return LowEvidencePolicyResult(
            triggered=triggered, answer=answer_out,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=supported_ratio,
            unverified_total=unverified_total,
            dialogue_supported_chunks=dialogue,
            user_asserted_chunks=user_asserted,
            reason=reason, locale=locale,
            suppressed_chars=suppressed_chars,
        )

    if local_critique_active:
        return _result(
            triggered=False, answer_out=answer,
            reason="local_critique_skip_empty_rewrite",
        )

    if not evidence_expected:
        # The task never needed external evidence (pure reasoning / design
        # question with an empty evidence chain and no realtime intent).
        # Truncating here would suppress a legitimate answer just because it
        # has nothing to cite — the model's synthesis IS the deliverable.
        # Factual / realtime questions keep the full gate (evidence_expected
        # stays True for them).
        return _result(
            triggered=False, answer_out=answer, reason="no_evidence_expected",
        )

    if total < min_total_chunks:
        return _result(
            triggered=False, answer_out=answer,
            reason="too_few_chunks_to_truncate",
        )

    if supported_ratio > max_verified_ratio:
        return _result(
            triggered=False, answer_out=answer,
            reason="verified_ratio_above_threshold",
        )

    if supported > 0 and unverified_total < unverified_floor:
        # Borderline: some support exists but the unverified mass is too small
        # to justify the truncation hammer. The evidence-support
        # telemetry will still surface it.
        return _result(
            triggered=False, answer_out=answer,
            reason="unverified_mass_below_floor",
        )

    locale = "ru" if (
        _looks_russian(question) or _looks_russian(answer)
    ) else "en"

    chunks_iter = getattr(report, "chunks", ()) or ()
    verified_texts: list[str] = []
    dialogue_texts: list[str] = []
    for c in chunks_iter:
        verdict = getattr(c, "verdict", "")
        text = getattr(c, "text", "")
        if not text.strip():
            continue
        if verdict == "verified":
            verified_texts.append(text.strip())
        # Dialogue-scoped claims survive the truncation alongside verified ones:
        # the answer loses its unsupported world claims and keeps the part the
        # session transcript backs (issue #119). Even when the gate fires, a
        # self-correction is never erased wholesale.
        elif verdict == "dialogue_supported":
            dialogue_texts.append(text.strip())

    suppressed_count = total - supported
    short_answer = _build_short_answer(
        verified_claim_texts=verified_texts + dialogue_texts,
        suppressed_count=suppressed_count,
        locale=locale,
        dialogue_count=len(dialogue_texts),
    )
    reason = (
        f"supported_ratio={supported_ratio:.2f} <= {max_verified_ratio} "
        f"and unverified_total={unverified_total} >= {unverified_floor}"
    )
    return _result(
        triggered=True,
        answer_out=short_answer,
        reason=reason,
        locale=locale,
        suppressed_chars=max(0, len(answer) - len(short_answer)),
    )
