"""MIR-069, phase 1 — the five-point verification explanation.

* only ``verified`` counts as confirmed; * ``user_asserted`` and
``dialogue_supported`` are named in point 4 as NOT externally confirmed —
support by the operator's words or this session's transcript is stated as
exactly that, never upgraded; * a report with nothing examined produces no
answer tail (the existing disclaimers already speak for that case) but still
explains itself for the journal.
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
    "admitted_unverified": "ответ сам назвал непроверенным (раздел «Не подтверждено»)",
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
    "refuted": (
        "ОПРОВЕРГНУТО собственной уликой: процитированный источник "
        "говорит иное"
    ),
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


def _tail_notes(report: VerificationReport, admitted: int) -> tuple[str, str]:
    """Приписки хвоста: названное непроверенным и опора на запись разговора.

    Опора на реплики разговора — не внешнее подтверждение (MIR-028), но и не
    пустота: 2026-09-21 на «помнишь разговор?» хвост «0 из 11, нулевая» читался
    как ответ без опоры, хотя стоял на записи беседы.
    """
    named = f" ({admitted} — ответ сам назвал непроверенными)" if admitted else ""
    dialogue = int(getattr(report, "dialogue_supported_chunks", 0) or 0)
    heard = f"; из них {dialogue} — по записи этого разговора" if dialogue else ""
    return named, heard


def _gaps_with_admitted(
    report: VerificationReport, examined: int,
) -> tuple[list[tuple[str, int]], int]:
    """Пробелы вместе с тем, что ответ сам вынес в «Не подтверждено».

    Названное непроверенным — тоже непроверенное, и счёт обязан его видеть
    (2026-09-21: «я не проверял X» над «9 из 9, уверенность высокая»).
    """
    gaps = _gap_counts(report)
    admitted = getattr(report, "admitted_unverified_chunks", 0) if examined else 0
    if admitted:
        gaps = [*gaps, ("admitted_unverified", admitted)]
    return gaps, admitted


#: Below this, the tail says the answer may not be addressing the question.
#: NOT an operator-set number. Chosen from measurement: two production runs on
#: 2026-08-09 answered a question that had not been asked and scored 0.051 and
#: 0.231, while an on-topic pair scores 1.000 and an off-topic one 0.000 on the
#: same metric. The gap is wide because the score is token overlap, so the
#: threshold sits well above the observed failures and well below a real answer.
#: Raise or lower it on evidence, not on taste.
_LOW_RELEVANCE = 0.35


def build_verification_summary(
    report: VerificationReport,
    chain: ProvenanceChain | None = None,
    vector: object | None = None,
    evidence_support: object | None = None,
    *,
    rejected_draft: bool = False,
) -> VerificationSummary:
    """Compose the five points from the verifier's own numbers. Pure.

    ``vector`` is the three-axis confidence diagnosis. It is optional
    because the summary predates it and must still build without one, and it
    is REPORTED RATHER THAN MERGED: citation integrity and task relevance
    answer different questions, and folding them into a single word would
    destroy the very information this argument exists to carry.
    """
    examined = sum(1 for c in report.chunks if c.verdict != "structural")
    verified = report.verified_chunks
    no_evidence_owed = bool(
        evidence_support is not None
        and not getattr(evidence_support, "applicable", True)
        and getattr(evidence_support, "reason", "") == "no_evidence_expected"
        and not getattr(
            evidence_support, "citation_integrity_violation", False
        )
    )

    # (1) Что проверял.
    if examined == 0:
        checked = (
            "ни одного утверждения — ответ не содержал проверяемых утверждений"
            + (" (цепочка доказательств пуста)" if report.chain_was_empty else "")
        )
    else:
        subject = "отклонённого черновика" if rejected_draft else "ответа"
        checked = f"{examined} утверждений {subject}, каждое отдельно"

    # (2) Каким способом.
    if examined == 0:
        method = "проверка не проводилась — сверять было нечего"
    else:
        parts = [
            ("сверка инлайн-цитат ответа с цепочкой доказательств "
            "(сохранённые выводы инструментов)")
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
    gaps, admitted = _gaps_with_admitted(report, examined)
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
    word, confidence = _confidence_wording(verified, examined + admitted)
    if no_evidence_owed and examined > 0:
        # «Нулевая уверенность» — приговор для хода, который был должен улики
        # и не принёс. Ход, который улик не был должен, приговора не заслужил.
        confidence = (
            "не применимо — внешнее подтверждение на этом ходе не требовалось"
        )

    if examined == 0:
        tail = ""
    elif no_evidence_owed:
        gap_total = sum(count for _v, count in gaps)
        tail = (
            f"{TAIL_PREFIX} внешнее подтверждение этому ответу не требовалось "
            f"(синтез/общие знания); утверждений без внешних источников: "
            f"{gap_total} — для такой задачи это норма."
        )
    else:
        gap_total = sum(count for _v, count in gaps)
        subject = "отклонённый черновик — " if rejected_draft else ""
        named, heard = _tail_notes(report, admitted)
        tail = (
            f"{TAIL_PREFIX} {subject}подтверждено {verified} из "
            f"{examined + admitted} утверждений{named}; "
            f"без внешнего подтверждения: {gap_total}{heard}; уверенность: {word}."
        )

    if tail:
        # Применимость спрашивается ДО значения: между разными системами письма
        # покрытие слов не измеряет соответствие задаче, и низкое число там —
        # факт о клавиатуре, а не об ответе (замер 2026-08-10). Ось ортогональна
        # уликам, поэтому предупреждение живёт и в хвосте «не требовалось».
        _relevance = getattr(vector, "relevance_score", None)
        if not getattr(vector, "relevance_applicable", True):
            _relevance = None
        # `verified != examined` — полное подтверждение снимает прокси: ответ
        # построен на уликах, взятых под этот вопрос. Частичного мало; почему,
        # чем мерялось и какой промах остался: docs/CODE_NOTES.md, «The measure
        # punished being answered».
        if (_relevance is not None and _relevance < _LOW_RELEVANCE
                and verified != examined):
            subject = "черновик" if rejected_draft else "ответ"
            tail += (
                f" Соответствие вопросу: {_relevance:.2f} — {subject} может отвечать "
                "не на заданный вопрос."
            )
        if rejected_draft:
            tail += " Это оценка черновика, не отправленного уведомления об отказе."

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
