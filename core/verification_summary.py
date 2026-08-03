"""MIR-069, phase 1 — the five-point verification explanation.

Operator ruling (2026-08-03): for every confirmation the agent must state in
human language (1) what it checked, (2) by what method, (3) on what evidence,
(4) what remains unverified, and (5) how confident it is. The loop has always
HAD these ingredients — per-claim verdicts, matched evidence ids, disclaimers —
but composed none of them into something a person can read: the numbers lived
only as JSONL counters, and the operator saw at most a one-line disclaimer.

This module is the missing composer. It is a pure function of the
:class:`~core.verifier_models.VerificationReport` (plus, optionally, the
provenance chain to resolve matched evidence ids into named sources): no LLM
call, no I/O, no state. The loop logs the full five points as a
``verification_explained`` journal event and appends the compact one-line
tail to the answer through the ResponseDraft notice ledger — so a later body
rewrite (truncation, enforcement) cannot delete the explanation.

The five markers are deliberately the SAME vocabulary the daemon liveness
probe introduced (MIR-070, `core/campaign_io.py`): every self-explanation in
the system should read the same way.

Honesty rules baked in (MIR-028 ruling):

* only ``verified`` counts as confirmed;
* ``user_asserted`` and ``dialogue_supported`` are named in point 4 as NOT
  externally confirmed — support by the operator's words or this session's
  transcript is stated as exactly that, never upgraded;
* a report with nothing examined produces no answer tail (the existing
  disclaimers already speak for that case) but still explains itself for the
  journal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.evidence import ProvenanceChain
from core.verifier_models import VerificationReport

#: The shared five-point vocabulary (first used by the MIR-070 daemon probe).
FIVE_POINT_MARKERS: tuple[str, str, str, str, str] = (
    "Проверял:",
    "Способ:",
    "Доказательство:",
    "Непроверенным осталось:",
    "Уверенность:",
)

#: Russian wording for every verdict the verifier assigns. A guard test scrapes
#: `core/verifier_core.py` and fails if a verdict appears there without a line
#: here — an explanation that omits a bucket lies by omission.
_VERDICT_RU: dict[str, str] = {
    "verified": "подтверждено сверкой цитаты с сохранённым выводом инструмента",
    "unverified": "без какого-либо подтверждения",
    "cited_but_unmatched": "цитата указана, но с источником не совпала",
    "self_declared": "только заявление самого агента, без внешнего источника",
    "structural": "служебная строка ответа, не утверждение",
    "dialogue_supported": (
        "подтверждено только записью этого диалога, не внешней проверкой"
    ),
    "user_asserted": (
        "только со слов оператора в этом ходе — подтверждён факт слов, "
        "не истинность содержания"
    ),
    "topic_supported_but_claim_unverified": (
        "источник по теме найден, но само утверждение (число/факт) не подтверждено"
    ),
    "subagent_asserted": "только слова субагента, квитанции инструмента нет",
    "receipt_missing": "цитата есть, но квитанция инструмента не найдена",
}

#: Verdicts that stay OUT of point 4: `verified` is the confirmed bucket,
#: `structural` lines are not claims at all.
_NOT_A_GAP: frozenset[str] = frozenset({"verified", "structural"})

#: How many matched sources point 3 lists before summarising the rest.
_MAX_NAMED_SOURCES = 5

#: The fixed prefix of the compact answer tail. `format_human_response`
#: buckets on it explicitly — measured live (2026-08-03): without that, the
#: Output Contract strip dropped the tail between `render()` and the print.
TAIL_PREFIX = "Проверка:"


@dataclass(frozen=True)
class VerificationSummary:
    """The five points, composed and ready for the journal and the answer."""

    checked: str
    method: str
    evidence: str
    unverified: str
    confidence: str
    #: Compact one-liner for the answer itself; empty when nothing was examined.
    tail: str
    examined_chunks: int
    verified_chunks: int

    def full_text(self) -> str:
        m = FIVE_POINT_MARKERS
        return "\n".join((
            f"{m[0]} {self.checked}",
            f"{m[1]} {self.method}",
            f"{m[2]} {self.evidence}",
            f"{m[3]} {self.unverified}",
            f"{m[4]} {self.confidence}",
        ))

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "full_text": self.full_text(),
            "tail_chars": len(self.tail),
            "examined_chunks": self.examined_chunks,
            "verified_chunks": self.verified_chunks,
        }


def _confidence_wording(verified: int, examined: int) -> tuple[str, str]:
    """(short word for the tail, full sentence for point 5)."""
    if examined <= 0:
        return "", "не применимо — проверяемых утверждений не было"
    if verified == examined:
        return "высокая", (
            f"высокая — подтверждены все {examined} проверяемых утверждений"
        )
    if verified == 0:
        return "нулевая", (
            f"нулевая — ни одно из {examined} утверждений не подтверждено "
            "внешним источником"
        )
    word = "средняя" if verified * 2 >= examined else "низкая"
    return word, f"{word} — подтверждено {verified} из {examined} утверждений"


def _gap_counts(report: VerificationReport) -> list[tuple[str, int]]:
    """Non-confirmed buckets actually present, with their counts."""
    counts: dict[str, int] = {}
    for chunk in report.chunks:
        if chunk.verdict not in _NOT_A_GAP:
            counts[chunk.verdict] = counts.get(chunk.verdict, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def build_verification_summary(
    report: VerificationReport,
    chain: ProvenanceChain | None = None,
) -> VerificationSummary:
    """Compose the five points from the verifier's own numbers. Pure."""
    examined = sum(1 for c in report.chunks if c.verdict != "structural")
    verified = report.verified_chunks

    # (1) Что проверял.
    if examined == 0:
        checked = (
            "ни одного утверждения — ответ не содержал проверяемых утверждений"
            + (" (цепочка доказательств пуста)" if report.chain_was_empty else "")
        )
    else:
        checked = f"{examined} утверждений ответа, каждое отдельно"

    # (2) Каким способом.
    if examined == 0:
        method = "проверка не проводилась — сверять было нечего"
    else:
        parts = [
            "сверка инлайн-цитат ответа с цепочкой доказательств "
            "(сохранённые выводы инструментов)"
        ]
        if report.dialogue_supported_chunks > 0:
            parts.append("сверка с записью этого диалога")
        if report.user_asserted_chunks > 0:
            parts.append(
                "сопоставление со словами оператора (подтверждает только факт слов)"
            )
        method = "; ".join(parts)

    # (3) На каком доказательстве.
    sources: list[str] = []
    if chain is not None and chain.evidences:
        by_id = {ev.id: ev for ev in chain.evidences}
        seen: set[str] = set()
        for chunk in report.chunks:
            if chunk.verdict != "verified":
                continue
            for ev_id in chunk.matched_evidence_ids:
                ev = by_id.get(ev_id)
                if ev is None:
                    continue
                name = f"{ev.obtained_via}: {ev.source_id}"
                if name not in seen:
                    seen.add(name)
                    sources.append(name)
    if sources:
        shown = sources[:_MAX_NAMED_SOURCES]
        extra = len(sources) - len(shown)
        evidence = ", ".join(shown) + (f" и ещё {extra}" if extra > 0 else "")
    elif report.chain_was_empty:
        evidence = "цепочка доказательств пуста — внешних источников не было"
    else:
        evidence = "совпадений утверждений с источниками цепочки нет"

    # (4) Что осталось непроверенным.
    gaps = _gap_counts(report)
    if examined == 0:
        unverified = "весь ответ — он не проверялся"
    elif not gaps:
        unverified = "ничего — все проверяемые утверждения подтверждены"
    else:
        unverified = "; ".join(
            f"{count} — {_VERDICT_RU.get(verdict, verdict)}"
            for verdict, count in gaps
        )

    # (5) Насколько уверен.
    word, confidence = _confidence_wording(verified, examined)

    if examined == 0:
        tail = ""
    else:
        gap_total = sum(count for _v, count in gaps)
        tail = (
            f"{TAIL_PREFIX} подтверждено {verified} из {examined} утверждений; "
            f"без внешнего подтверждения: {gap_total}; уверенность: {word}."
        )

    return VerificationSummary(
        checked=checked,
        method=method,
        evidence=evidence,
        unverified=unverified,
        confidence=confidence,
        tail=tail,
        examined_chunks=examined,
        verified_chunks=verified,
    )
