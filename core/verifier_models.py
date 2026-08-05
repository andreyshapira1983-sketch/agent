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
    """WHY a claim did not survive its check — structured, not a sentence.

    A verdict is a stamp: it tells the agent it was wrong. This tells it what
    to change. The distinction is the operator's criterion for MIR-060 — a
    label that flips means the instrument got honest; only a usable reason can
    make the next attempt better, and only if it reaches the next attempt.

    Fields are separate rather than one string because the consumers differ:
    the replan context wants `explanation`, a future gate may branch on `code`,
    and a human reading the journal wants `expected`/`actual` side by side.
    """

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
            "fully_unverified": self.fully_unverified,
            "chain_was_empty": self.chain_was_empty,
            "malformed_output": self.malformed_output,
            "disclaimer_set": self.disclaimer is not None,
            "verdicts": [c.verdict for c in self.chunks],
        }
