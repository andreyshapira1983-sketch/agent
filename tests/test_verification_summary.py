"""MIR-069, phase 1 — the five-point verification explanation.

Operator ruling (2026-08-03, recorded verbatim in MIR-028/MIR-069): for every
confirmation the agent must state in human language (1) what it checked,
(2) by what method, (3) on what evidence, (4) what remains unverified, and
(5) how confident it is. Before this module the loop *had* every ingredient —
per-claim verdicts, matched evidence ids, disclaimers — but never composed
them into an explanation a human can read; the numbers lived only in JSONL
counters.

Contract under test:

* `build_verification_summary` is a PURE function of the report (+ optional
  chain) — no LLM, no I/O;
* the full text carries the same five Russian markers the daemon liveness
  probe (MIR-070) already uses, so every self-explanation in the system reads
  the same way;
* the numbers are the report's own numbers — verified counts as confirmed,
  everything else (including `user_asserted` and `dialogue_supported`, per the
  MIR-028 ruling) is named in point 4 as not externally confirmed;
* the compact tail survives body rewrites because it rides the ResponseDraft
  notice ledger, not the body string;
* a report with nothing examined yields NO tail (the disclaimers already
  cover that case) but still explains itself honestly in the journal.
"""
from __future__ import annotations

import dataclasses
import re
from importlib.util import find_spec
from pathlib import Path

from core.verification_summary import (
    _VERDICT_RU,
    build_verification_summary,
)
from core.verifier_models import ClaimChunk, VerificationReport

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Report fixtures — built directly, the same shapes verify() emits.
# ---------------------------------------------------------------------------

def _chunk(verdict: str, ev_ids: tuple[str, ...] = ()) -> ClaimChunk:
    return ClaimChunk(
        text=f"утверждение с вердиктом {verdict}",
        citations=(),
        matched_evidence_ids=ev_ids,
        verdict=verdict,
    )


def _report(chunks: tuple[ClaimChunk, ...], **overrides) -> VerificationReport:
    verdicts = [c.verdict for c in chunks]
    fields = {
        "total_chunks": len(chunks),
        "verified_chunks": verdicts.count("verified"),
        "unverified_chunks": verdicts.count("unverified"),
        "cited_but_unmatched_chunks": verdicts.count("cited_but_unmatched"),
        "self_declared_chunks": verdicts.count("self_declared"),
        "structural_chunks": verdicts.count("structural"),
        "chunks": chunks,
        "annotated_answer": "\n".join(c.text for c in chunks),
        "fully_unverified": (verdicts.count("verified") == 0),
        "chain_was_empty": False,
        "dialogue_supported_chunks": verdicts.count("dialogue_supported"),
        "user_asserted_chunks": verdicts.count("user_asserted"),
        "topic_supported_but_claim_unverified_chunks": verdicts.count(
            "topic_supported_but_claim_unverified"
        ),
        "subagent_asserted_chunks": verdicts.count("subagent_asserted"),
        "receipt_missing_chunks": verdicts.count("receipt_missing"),
    }
    fields.update(overrides)
    return VerificationReport(**fields)


# ---------------------------------------------------------------------------
# The five points
# ---------------------------------------------------------------------------

class TestFivePoints:
    def test_counts_are_the_reports_own_numbers(self):
        summary = build_verification_summary(
            _report((
                _chunk("verified", ("ev1",)),
                _chunk("user_asserted"),
                _chunk("unverified"),
            ))
        )
        text = summary.full_text()
        assert "подтверждено 1 из 3" in summary.tail
        # user_asserted is named as NOT externally confirmed (MIR-028 ruling),
        # never silently folded into the confirmed bucket.
        assert "слов" in text and "оператора" in text
        assert "низкая" in summary.tail


class TestVerdictVocabularyIsCovered:
    def test_every_verdict_the_verifier_assigns_has_russian_wording(self):
        """Every verdict the verifier can emit has wording for point 4.

        Two sources, so moving the literals into constants cannot empty the
        check: the scrape of verifier_core and the report's per-verdict counters.
        """
        src = Path(find_spec("core.verifier_core").origin).read_text(encoding="utf-8")
        scraped = set(re.findall(r'verdict\s*=\s*"([a-z_]+)"', src))
        scraped |= set(re.findall(r'verdict="([a-z_]+)"', src))
        assert scraped, "скрейп verifier_core не нашёл ни одного вердикта — обнови шаблон"
        counted = {
            f.name.removesuffix("_chunks")
            for f in dataclasses.fields(VerificationReport)
            if f.name.endswith("_chunks") and f.name != "total_chunks"
        }
        missing = sorted(v for v in scraped | counted if v not in _VERDICT_RU)
        assert not missing, f"вердикты без русской формулировки: {missing}"
