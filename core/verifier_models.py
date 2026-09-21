"""Verifier value types: a citation, a claim chunk, and the verification report.

Extracted from `core/verifier` by autonomous self-build module split.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Citation:
    """One parsed inline citation."""
    prefix: str
    body: str
    raw: str
    expected_kind: str


@dataclass(frozen=True)
class ClaimReason:
    """WHY a claim did not survive its check — structured, not a sentence."""

    code: str                 # `sum_mismatch`, `count_mismatch`, ...
    expected: str = ""        # what the source implies
    actual: str = ""          # what the claim asserted
    explanation: str = ""     # one sentence, written for the next attempt
    computed_from: str = ""   # the values the computation used

    def to_log_payload(self) -> dict[str, str]:
        return {
            "code": self.code,
            "expected": self.expected,
            "actual": self.actual,
            "explanation": self.explanation,
            "computed_from": self.computed_from,
        }


@dataclass(frozen=True)
class ClaimChunk:
    """One sentence-or-paragraph claim from the answer."""
    text: str
    citations: tuple[Citation, ...]
    matched_evidence_ids: tuple[str, ...]
    verdict: str
    #: Present only when a check REFUTED the claim and could say why. A
    #: verdict without one is a stamp; the point of MIR-060 direction (b) is
    #: that arithmetic refutations always carry their working.
    reason: ClaimReason | None = None


@dataclass(frozen=True)
class VerificationReport:
    """The full diagnosis."""
    total_chunks: int
    verified_chunks: int
    unverified_chunks: int
    cited_but_unmatched_chunks: int
    self_declared_chunks: int
    structural_chunks: int
    chunks: tuple[ClaimChunk, ...]
    annotated_answer: str
    fully_unverified: bool
    chain_was_empty: bool
    disclaimer: str | None = None
    malformed_output: bool = False
    topic_supported_but_claim_unverified_chunks: int = 0
    subagent_asserted_chunks: int = 0
    receipt_missing_chunks: int = 0
    #: Claims about THIS session's dialogue, backed by the verbatim prior turn
    #: (issue #119). Deliberately its own counter: such a claim is neither
    #: `verified` (no external source confirms it) nor `unverified` (the
    #: recording of the exchange does support it), so collapsing it into either
    #: bucket is what produced the original defect.
    dialogue_supported_chunks: int = 0
    #: Claims supported ONLY by the operator's words this turn (the injected
    #: `user_explicit` evidence). Operator ruling 2026-08-03 (MIR-028): user
    #: words confirm that the user said it — never the content's objective
    #: truth — so this is its own counter, never folded into `verified`, and
    #: it neither grants full evidence score nor banks a clean success alone.
    user_asserted_chunks: int = 0
    #: Claims a content gate PROVED do not follow from the evidence they cite
    #: (a `ClaimReason` names what was expected and what was found). Operator
    #: ruling 2026-08-12: refuted is a polarity, not a shade of unverified —
    #: folding it into `topic_supported...` let five good claims dilute one
    #: known lie all the way into `usage_eligible=True` (live turn 3,
    #: trace_d322a875).
    refuted_chunks: int = 0
    #: Строки, которые ответ САМ вынес в раздел «Unverified» («Не
    #: подтверждено»). Утверждениями они не являются и в `chunks` не входят
    #: (раздел — не-утвердительный), но это непроверенное, названное вслух.
    #: 2026-09-21: без этого счётчика ответ нёс «я не проверял X» и внизу
    #: «подтверждено 9 из 9; уверенность: высокая» — считалось лёгкое.
    admitted_unverified_chunks: int = 0

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "total_chunks": self.total_chunks,
            "verified_chunks": self.verified_chunks,
            "unverified_chunks": self.unverified_chunks,
            "cited_but_unmatched_chunks": self.cited_but_unmatched_chunks,
            "self_declared_chunks": self.self_declared_chunks,
            "structural_chunks": self.structural_chunks,
            "topic_supported_but_claim_unverified_chunks": self.topic_supported_but_claim_unverified_chunks,
            "subagent_asserted_chunks": self.subagent_asserted_chunks,
            "receipt_missing_chunks": self.receipt_missing_chunks,
            "dialogue_supported_chunks": self.dialogue_supported_chunks,
            "user_asserted_chunks": self.user_asserted_chunks,
            "refuted_chunks": self.refuted_chunks,
            "admitted_unverified_chunks": self.admitted_unverified_chunks,
            "fully_unverified": self.fully_unverified,
            "chain_was_empty": self.chain_was_empty,
            "malformed_output": self.malformed_output,
            "disclaimer_set": self.disclaimer is not None,
            "verdicts": [c.verdict for c in self.chunks],
            # ПОЧЕМУ опровергнуто, а не только сколько. Замер 2026-09-20:
            # `content_refuted` закрывает эпизоду вход в опыт раньше всех
            # прочих осей и несли его 105 из 142 недопущенных эпизодов — а
            # код причины в журнал не попадал вовсе. Установить его удалось
            # только реконструкцией: цепочки улик собирались заново из трасс
            # и пересуживались верификатором. Так нашлись `sum_mismatch`,
            # сложивший `exit_code` с `duration_ms`, и
            # `absence_refuted_by_evidence`, взявший предметом отсутствия
            # перечисленное наличное. Мера, которая не записывает
            # отвергнутое, не даёт себя перемерить.
            "refutations": self._refutation_records(),
        }

    #: Сколько опровержений попадает в запись. Причины повторяются, а журнал
    #: читают целиком: пять разных кодов видно, тысяча одинаковых — шум.
    _REFUTATIONS_LOGGED = 5

    def _refutation_records(self) -> list[dict[str, str]]:
        """Причина каждого опровержения: код, работа и само утверждение."""
        out: list[dict[str, str]] = []
        for chunk in self.chunks:
            if chunk.verdict != "refuted" or chunk.reason is None:
                continue
            record = chunk.reason.to_log_payload()
            record["claim"] = " ".join((chunk.text or "").split())[:160]
            out.append(record)
            if len(out) >= self._REFUTATIONS_LOGGED:
                break
        return out
