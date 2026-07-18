"""Claim-level answer enforcement (critique plan PR3).

Separates outcomes the chunk-count gate used to conflate:

* ``insufficient_evidence`` — long unsupported factual deliverable
* ``verifier_failure`` — verify() raised / timed out (soft-fail: keep draft)
* ``malformed_report`` / ``citation_parse_failure`` — soft-fail markers
* ``local_critique_preserved`` — referent resolved; empty rewrite forbidden
* ``unsupported_world_claims`` — short categorical world claims without
  non-user support (hedge, do not nuke)

Feature flag ``AGENT_ENFORCE_UNSUPPORTED_CLAIMS``: ``off`` | ``shadow`` | ``on``.
Local-critique empty-rewrite skip is an invariant and does **not** require
the flag. Verifier exception soft-fail is also always on.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from core.low_evidence_policy import evaluate_low_evidence_policy

FEATURE_FLAG = "enforce_unsupported_world_claims"
FEATURE_FLAG_DEFAULT = False
_ENV_MODE = "AGENT_ENFORCE_UNSUPPORTED_CLAIMS"

EnforcementOutcome = Literal[
    "none",
    "insufficient_evidence",
    "verifier_failure",
    "malformed_report",
    "citation_parse_failure",
    "local_critique_preserved",
    "unsupported_world_claims",
]

# Categorical / absolute phrasing that should not ship as bare world fact
# without non-user evidence (short answers the ≥8 chunk gate misses).
_CATEGORICAL_RE = re.compile(
    r"(?i)\b("
    r"definitely|certainly|always|never|guaranteed|proven|"
    r"без\s+сомнен\w*|гарантир\w*|доказан\w*|всегда|никогда|"
    r"абсолютн\w*|однозначн\w*|категоричн\w*"
    r")\b"
)


def enforce_unsupported_claims_mode() -> str:
    """Return ``off`` (default), ``shadow`` (log only), or ``on``."""
    raw = (os.getenv(_ENV_MODE) or "").strip().lower()
    if raw in ("on", "true", "1", "yes"):
        return "on"
    if raw in ("shadow",):
        return "shadow"
    return "off"


@dataclass(frozen=True)
class EnforcementResult:
    """Outcome of one post-verify enforcement pass."""

    outcome: EnforcementOutcome
    answer: str
    applied: bool
    would_change_answer: bool
    reason: str = ""
    mode: str = "off"
    notes: tuple[str, ...] = field(default_factory=tuple)
    low_evidence_payload: dict[str, Any] | None = None

    def to_log_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "outcome": self.outcome,
            "applied": self.applied,
            "would_change_answer": self.would_change_answer,
            "reason": self.reason,
            "mode": self.mode,
            "notes": list(self.notes),
        }
        if self.low_evidence_payload is not None:
            payload["low_evidence"] = self.low_evidence_payload
        return payload


def _looks_russian(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def _soft_fail_note(outcome: EnforcementOutcome, locale: str) -> str:
    if locale == "ru":
        if outcome == "verifier_failure":
            return (
                "\n\nUnverified: проверка источников завершилась технической "
                "ошибкой; черновик ответа сохранён без усечения."
            )
        return (
            "\n\nUnverified: разбор цитат/контракта неполный; черновик "
            "сохранён (soft-fail)."
        )
    if outcome == "verifier_failure":
        return (
            "\n\nUnverified: verifier failed technically; draft kept "
            "(not rewritten as a data gap)."
        )
    return (
        "\n\nUnverified: citation/contract parse incomplete; draft kept "
        "(soft-fail)."
    )


def _hedge_short_categorical(answer: str, *, locale: str, count: int) -> str:
    """Downgrade confidence and note unsupported world claims; keep body."""
    hedged = re.sub(
        r"(?im)^Confidence:\s*\w+\s*$",
        "Confidence: low",
        answer,
        count=1,
    )
    if locale == "ru":
        note = (
            f"{count} категоричных утверждений о мире без внешних "
            "источников; формулировки следует считать гипотезами."
        )
    else:
        note = (
            f"{count} categorical world claim(s) lack non-user evidence; "
            "treat them as unverified."
        )
    if re.search(r"(?im)^Unverified:\s*", hedged):
        hedged = re.sub(
            r"(?im)^(Unverified:\s*)(.*)$",
            lambda m: (
                m.group(1)
                + (
                    (m.group(2).strip() + " " + note).strip()
                    if m.group(2).strip().lower()
                    not in ("nothing", "ничего", "-", "")
                    else note
                )
            ),
            hedged,
            count=1,
        )
        return hedged
    return hedged.rstrip() + f"\n\nUnverified: {note}"


def _count_categorical_unsupported(report: Any) -> int:
    chunks = getattr(report, "chunks", ()) or ()
    n = 0
    for c in chunks:
        verdict = getattr(c, "verdict", "") or ""
        if verdict in ("verified", "self_declared", "structural"):
            continue
        text = getattr(c, "text", "") or ""
        # Skip pure target-descriptive critique lines.
        if re.search(r"(?i)\[(?:user:target|prior_turn:|artifact:)", text):
            continue
        if _CATEGORICAL_RE.search(text):
            n += 1
    return n


def apply_answer_enforcement(
    *,
    answer: str,
    report: Any | None,
    question: str = "",
    evidence_expected: bool = True,
    local_critique_active: bool = False,
    verifier_failure: bool = False,
    mode: str | None = None,
) -> EnforcementResult:
    """Apply PR3 enforcement. Never raises."""
    mode_s = mode if mode is not None else enforce_unsupported_claims_mode()
    locale = "ru" if (_looks_russian(question) or _looks_russian(answer)) else "en"

    if verifier_failure:
        note = _soft_fail_note("verifier_failure", locale)
        would = True
        applied = mode_s == "on"
        out_answer = (answer + note) if applied and note.strip() not in answer else answer
        return EnforcementResult(
            outcome="verifier_failure",
            answer=out_answer,
            applied=applied and out_answer != answer,
            would_change_answer=would,
            reason="verify_raised_or_unavailable",
            mode=mode_s,
            notes=("soft_fail_keep_draft",),
        )

    if report is None:
        return EnforcementResult(
            outcome="none",
            answer=answer,
            applied=False,
            would_change_answer=False,
            reason="no_report",
            mode=mode_s,
        )

    if bool(getattr(report, "malformed_output", False)):
        note = _soft_fail_note("malformed_report", locale)
        would = True
        applied = mode_s == "on"
        out_answer = (answer + note) if applied and note not in answer else answer
        return EnforcementResult(
            outcome="malformed_report",
            answer=out_answer,
            applied=applied and out_answer != answer,
            would_change_answer=would,
            reason="malformed_output_contract",
            mode=mode_s,
            notes=("soft_fail_keep_draft",),
        )

    # Invariant: resolved local critique must not become empty-Facts rewrite.
    if local_critique_active:
        le_skipped = evaluate_low_evidence_policy(
            answer=answer,
            report=report,
            question=question,
            evidence_expected=evidence_expected,
            local_critique_active=True,
        )
        le_would = evaluate_low_evidence_policy(
            answer=answer,
            report=report,
            question=question,
            evidence_expected=evidence_expected,
            local_critique_active=False,
        )
        return EnforcementResult(
            outcome="local_critique_preserved",
            answer=answer,
            applied=False,
            would_change_answer=bool(le_would.triggered),
            reason=le_skipped.reason or "local_critique_skip_empty_rewrite",
            mode=mode_s,
            notes=("empty_rewrite_forbidden",),
            low_evidence_payload=le_skipped.to_log_payload(),
        )

    # Existing strict long-answer truncation (always on when it fires —
    # this is the pre-PR3 enforcement; flag only gates the NEW claim path).
    le = evaluate_low_evidence_policy(
        answer=answer,
        report=report,
        question=question,
        evidence_expected=evidence_expected,
        local_critique_active=False,
    )
    if le.triggered:
        return EnforcementResult(
            outcome="insufficient_evidence",
            answer=le.answer,
            applied=True,
            would_change_answer=True,
            reason=le.reason,
            mode=mode_s,
            low_evidence_payload=le.to_log_payload(),
        )

    # Claim-level short path (flag gated): categorical unsupported world claims
    # with fewer than MIN_TOTAL chunks — the hole the ≥8 gate leaves open.
    total = int(getattr(report, "total_chunks", 0) or 0)
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    cat_n = _count_categorical_unsupported(report)
    if (
        evidence_expected
        and 1 <= total < 8
        and verified == 0
        and cat_n > 0
    ):
        hedged = _hedge_short_categorical(answer, locale=locale, count=cat_n)
        would = hedged != answer
        applied = mode_s == "on" and would
        return EnforcementResult(
            outcome="unsupported_world_claims",
            answer=hedged if applied else answer,
            applied=applied,
            would_change_answer=would,
            reason=f"categorical_unsupported={cat_n} total_chunks={total}",
            mode=mode_s,
            notes=("claim_level_short_path",),
        )

    return EnforcementResult(
        outcome="none",
        answer=answer,
        applied=False,
        would_change_answer=False,
        reason=le.reason or "no_enforcement",
        mode=mode_s,
        low_evidence_payload=le.to_log_payload(),
    )
