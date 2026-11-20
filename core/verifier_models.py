"""Verifier value types: a citation, a claim chunk and the verification report; extracted from `core/verifier`."""
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
    #: Present only when a check REFUTED the claim and could say why; arithmetic refutations always carry their working (MIR-060 direction b).
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
    #: Claims about THIS session's dialogue backed by the verbatim prior turn; its own counter, neither `verified` (no external source) nor `unverified` (the exchange supports it).
    dialogue_supported_chunks: int = 0
    #: Claims supported ONLY by the operator's words this turn (`user_explicit` evidence): they confirm that the user said it, not the truth, so a separate counter that grants no full evidence score and banks no clean success alone.
    user_asserted_chunks: int = 0
    #: Claims a content gate PROVED do not follow from the cited evidence (a `ClaimReason` names expected and found); refuted is a polarity, not a shade of unverified, so good claims cannot dilute a known lie into `usage_eligible=True`.
    refuted_chunks: int = 0
    #: Lines the answer itself put into the "Unverified" section: not claims and not in `chunks`, but unverified things named aloud; counted so the summary does not say "9 of 9 confirmed" next to "I did not check X".
    admitted_unverified_chunks: int = 0
    #: R2 in shadow mode: "number does not match its parenthesised list" goes to the journal only and does not affect the verdict.
    shadow_count_mismatch: tuple[dict[str, str], ...] = ()

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
            # WHY refuted, not only how many: the reason code goes to the journal so a refutation can be re-measured without reconstructing evidence chains from traces.
            "refutations": self._refutation_records(),
            "shadow_count_mismatch": list(self.shadow_count_mismatch[: self._REFUTATIONS_LOGGED]),
        }

    #: How many refutations enter a record; reasons repeat and the journal is read whole, so five distinct codes are visible but a thousand identical ones are noise.
    _REFUTATIONS_LOGGED = 5

    def _refutation_records(self) -> list[dict[str, str]]:
        """Reason of each refutation: code, working and the claim itself."""
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
