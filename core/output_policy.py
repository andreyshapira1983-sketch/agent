"""Ranker-to-output policy.

The Verifier answers "did this citation resolve to evidence?".
The Source Ranker answers "is that evidence strong enough for this type of
question?".  This module is the bridge between the two: it applies ranker
constraints to the user-facing answer after verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.source_ranker import SourceRankingReport


@dataclass(frozen=True)
class OutputPolicyResult:
    answer: str
    applied: bool = False
    confidence_ceiling: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    downgraded_realtime_claims: int = 0

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "confidence_ceiling": self.confidence_ceiling,
            "warnings": list(self.warnings),
            "downgraded_realtime_claims": self.downgraded_realtime_claims,
        }


def apply_ranker_output_policy(
    *,
    answer: str,
    ranking: SourceRankingReport | None,
    question: str,
    replan_exhausted: bool = False,
) -> OutputPolicyResult:
    """Apply deterministic output constraints derived from source ranking."""
    updated = answer
    warnings: list[str] = []
    confidence_ceiling: str | None = None
    downgraded = 0

    if ranking is not None and ranking.realtime_required:
        direct = [rank for rank in ranking.ranks if rank.support_level == "direct"]
        insufficient = [
            rank
            for rank in ranking.ranks
            if rank.support_level == "insufficient_for_realtime"
        ]
        # A realtime question without any direct live source cannot have high
        # confidence, even if normal citation matching succeeded.
        if insufficient and not direct:
            confidence_ceiling = "low"
            note = _realtime_warning(question)
            warnings.append(note)
            updated, downgraded = _downgrade_realtime_verified_tags(updated)
            updated = _rewrite_confidence(updated, "low")
            updated = _merge_unverified(updated, note)
        elif direct:
            # Direct-but-capped realtime evidence (for example stale or
            # undated) can be useful, but still may not justify "high".
            ceiling = min(rank.confidence_ceiling for rank in direct)
            if ceiling <= 0.55:
                confidence_ceiling = "medium"
                note = _stale_realtime_warning(question)
                warnings.append(note)
                updated = _rewrite_confidence(updated, "medium")
                updated = _merge_unverified(updated, note)

    if replan_exhausted:
        note = _replan_warning(question)
        warnings.append(note)
        updated = _merge_unverified(updated, note)

    applied = updated != answer or bool(warnings)
    return OutputPolicyResult(
        answer=updated,
        applied=applied,
        confidence_ceiling=confidence_ceiling,
        warnings=tuple(warnings),
        downgraded_realtime_claims=downgraded,
    )


def _looks_russian(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def _realtime_warning(question: str) -> str:
    if _looks_russian(question):
        return (
            "Найденные источники открылись, но они недостаточны для "
            "подтверждения realtime-значения без специализированного live "
            "источника с timestamp."
        )
    return (
        "The sources were fetched, but they are insufficient for confirming a "
        "realtime value without a specialized live source with a timestamp."
    )


def _stale_realtime_warning(question: str) -> str:
    if _looks_russian(question):
        return (
            "Realtime-источник найден, но свежесть данных ограничивает "
            "уверенность ответа."
        )
    return (
        "A realtime-capable source was found, but freshness limits the answer "
        "confidence."
    )


def _replan_warning(question: str) -> str:
    if _looks_russian(question):
        return (
            "Часть проверки была остановлена из-за исчерпания replan-бюджета; "
            "некоторые данные могли остаться неподтверждёнными."
        )
    return (
        "Part of verification stopped because the replan budget was exhausted; "
        "some data may remain unconfirmed."
    )


def _downgrade_realtime_verified_tags(answer: str) -> tuple[str, int]:
    pattern = re.compile(r"\[verified:(web|search):[^\]]+\]")
    count = 0

    def repl(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[unverified:insufficient_for_realtime]"

    return pattern.sub(repl, answer), count


def _rewrite_confidence(answer: str, ceiling: str) -> str:
    """Rewrite high confidence to the given ceiling, preserving low/medium."""

    def repl_inline(match: re.Match[str]) -> str:
        prefix, value = match.group(1), match.group(2).lower()
        if _confidence_rank(value) > _confidence_rank(ceiling):
            return f"{prefix}{ceiling}"
        return match.group(0)

    updated = re.sub(
        r"(?im)^((?:\*\*Confidence:\*\*|(?:\*\*)?Confidence(?:\*\*)?\s*:?)\s*)(low|medium|high)\b",
        repl_inline,
        answer,
    )

    # Support the common two-line Markdown form:
    #   # Confidence
    #   high
    def repl_header(match: re.Match[str]) -> str:
        header, value = match.group(1), match.group(2).lower()
        if _confidence_rank(value) > _confidence_rank(ceiling):
            return f"{header}{ceiling}"
        return match.group(0)

    return re.sub(
        r"(?im)^((?:#+\s*)?Confidence\s*\n\s*)(low|medium|high)\b",
        repl_header,
        updated,
    )


def _merge_unverified(answer: str, note: str) -> str:
    if re.search(r"(?im)^Unverified\s*:\s*nothing\s*$", answer):
        return re.sub(
            r"(?im)^Unverified\s*:\s*nothing\s*$",
            f"Unverified: {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^Unverified\s*:\s*(?!nothing\s*$).+\S\s*$", answer):
        return re.sub(
            r"(?im)^(Unverified\s*:\s*.+\S)\s*$",
            rf"\1; {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^\*\*Unverified:\*\*\s*nothing\s*$", answer):
        return re.sub(
            r"(?im)^\*\*Unverified:\*\*\s*nothing\s*$",
            f"**Unverified:** {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^\*\*Unverified\*\*\s*:?\s*nothing\s*$", answer):
        return re.sub(
            r"(?im)^\*\*Unverified\*\*\s*:?\s*nothing\s*$",
            f"**Unverified:** {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^Unverified\s*:\s*$", answer):
        return re.sub(
            r"(?im)^Unverified\s*:\s*$",
            f"Unverified:\n- {note}",
            answer,
            count=1,
        )
    if re.search(r"(?im)^#+\s*Unverified\s*$", answer):
        return re.sub(
            r"(?im)^(#+\s*Unverified\s*)$",
            rf"\1\n- {note}",
            answer,
            count=1,
        )
    return answer.rstrip() + f"\nUnverified: {note}\n"


def _confidence_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(value.lower(), 0)
