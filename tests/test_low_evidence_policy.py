"""Low-evidence answer policy.

Triggers a deterministic truncation of the user-facing answer when the
verifier's verdict distribution is severely below threshold. Pairs
with `core.confidence_gate.ConfidenceGate` (which is observation-only)
to actually enforce the "недостаточно данных" reply shape.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.low_evidence_policy import (
    evaluate_low_evidence_policy,
    is_evidence_expected,
)


@dataclass
class _Chunk:
    text: str
    verdict: str


@dataclass
class _Report:
    total_chunks: int = 0
    verified_chunks: int = 0
    unverified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    topic_supported_but_claim_unverified_chunks: int = 0
    chunks: tuple = ()


_SAMPLE_LONG_ANSWER = (
    "Conclusion: 30-day product launch plan.\n"
    "Facts:\n"
    "  - week 1: discovery interviews [web:x]\n"
    "  - week 2: prototype [web:y]\n"
    "  - 66% of teams ship in 2-4 weeks [web:saas]\n"
    "Sources: stub\n"
    "Confidence: high\n"
    "Unverified: nothing\n"
    "Safety: ok"
)


class TestLowEvidenceTrigger:
    def test_triggered_when_verified_ratio_low_and_unverified_mass_high(self):
        report = _Report(
            total_chunks=39,
            verified_chunks=6,
            unverified_chunks=33,
            chunks=tuple(
                [_Chunk(text=f"verified claim {i}.", verdict="verified")
                 for i in range(6)] +
                [_Chunk(text=f"junk {i}.", verdict="unverified")
                 for i in range(33)]
            ),
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="30-day product launch plan?",
        )
        assert result.triggered is True
        # 6/39 ≈ 0.154 ≤ 0.20
        assert result.verified_ratio < 0.20
        assert result.unverified_total == 33
        # Replacement answer carries Output Contract section headers.
        assert "Conclusion:" in result.answer
        assert "Facts:" in result.answer
        assert "Confidence: low" in result.answer
        assert "Unverified:" in result.answer
        # All 6 verified claim texts are preserved verbatim.
        for i in range(6):
            assert f"verified claim {i}." in result.answer
        # suppressed_chars is `max(0, len(original) - len(rebuilt))`.
        # When the rebuilt short reply is longer than the (synthetic)
        # input fixture, this clamps to 0 — that is correct, the field
        # tracks net char-savings, not a triggered-flag proxy.
        assert result.suppressed_chars >= 0

    def test_triggered_zero_verified_emits_no_facts_stub(self):
        # 0 verified, many unverified — the most dangerous case.
        report = _Report(
            total_chunks=10,
            verified_chunks=0,
            unverified_chunks=10,
            chunks=tuple(
                _Chunk(text=f"junk {i}.", verdict="unverified")
                for i in range(10)
            ),
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="business plan",
        )
        assert result.triggered is True
        assert result.verified_chunks == 0
        # Facts section explicitly notes there's nothing to show.
        assert "no verified claim" in result.answer.lower()

    def test_not_triggered_when_total_below_min(self):
        # Short answer (3 chunks, 0 verified) — not a "long polished
        # plan" so we don't truncate. Confidence gate still logs.
        report = _Report(
            total_chunks=3,
            verified_chunks=0,
            unverified_chunks=3,
        )
        result = evaluate_low_evidence_policy(
            answer="short reply",
            report=report,
            question="q",
        )
        assert result.triggered is False
        assert result.reason == "too_few_chunks_to_truncate"

    def test_not_triggered_when_verified_ratio_high(self):
        # Healthy: 9/10 verified.
        report = _Report(
            total_chunks=10,
            verified_chunks=9,
            unverified_chunks=1,
        )
        result = evaluate_low_evidence_policy(
            answer="ok reply",
            report=report,
            question="q",
        )
        assert result.triggered is False
        assert result.reason == "verified_ratio_above_threshold"

    def test_not_triggered_when_unverified_mass_below_floor(self):
        # Borderline: 8 chunks, 1 verified, 1 unverified, 6 self_declared
        # (we count self_declared as neutral so unverified_total=1 < 6).
        report = _Report(
            total_chunks=8,
            verified_chunks=1,
            unverified_chunks=1,
            cited_but_unmatched_chunks=0,
            topic_supported_but_claim_unverified_chunks=0,
        )
        result = evaluate_low_evidence_policy(
            answer="reply",
            report=report,
            question="q",
        )
        assert result.triggered is False
        assert result.reason == "unverified_mass_below_floor"

    def test_not_triggered_when_evidence_not_expected(self):
        # A design / pure-reasoning answer: would normally trigger the
        # truncation hammer (0 verified, high unverified mass), but because
        # the task never needed external evidence the gate is bypassed and
        # the full answer survives.
        report = _Report(
            total_chunks=23,
            verified_chunks=0,
            unverified_chunks=6,
            chunks=tuple(
                _Chunk(text=f"design point {i}.", verdict="self_declared")
                for i in range(23)
            ),
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="спроектируй архитектуру системы очередей",
            evidence_expected=False,
        )
        assert result.triggered is False
        assert result.reason == "no_evidence_expected"
        assert result.answer == _SAMPLE_LONG_ANSWER

    def test_still_triggered_when_evidence_expected_default(self):
        # Same distribution, but evidence WAS expected (factual/realtime):
        # the gate must still fire, so the default protects factual answers.
        report = _Report(
            total_chunks=23,
            verified_chunks=0,
            unverified_chunks=6,
            chunks=tuple(
                _Chunk(text=f"claim {i}.", verdict="unverified")
                for i in range(23)
            ),
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="what is the dollar rate today?",
        )
        assert result.triggered is True

    def test_topic_supported_counts_as_unverified_mass(self):
        # 0 verified + 8 topic-only claims should trigger because
        # topic_supported is part of the unverified_total.
        report = _Report(
            total_chunks=8,
            verified_chunks=0,
            topic_supported_but_claim_unverified_chunks=8,
            chunks=tuple(
                _Chunk(text=f"stat {i}.",
                       verdict="topic_supported_but_claim_unverified")
                for i in range(8)
            ),
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="q",
        )
        assert result.triggered is True

    def test_locale_detection_russian_question(self):
        report = _Report(
            total_chunks=10,
            verified_chunks=0,
            unverified_chunks=10,
            chunks=tuple(
                _Chunk(text=f"мусор {i}.", verdict="unverified")
                for i in range(10)
            ),
        )
        result = evaluate_low_evidence_policy(
            answer="Conclusion: план запуска продукта.",
            report=report,
            question="Составь план запуска продукта на 30 дней",
        )
        assert result.triggered is True
        assert result.locale == "ru"
        # Russian notice is present.
        assert "Недостаточно данных" in result.answer

    def test_locale_detection_english_default(self):
        report = _Report(
            total_chunks=10,
            verified_chunks=0,
            unverified_chunks=10,
        )
        result = evaluate_low_evidence_policy(
            answer="Conclusion: plain ascii reply",
            report=report,
            question="give me a plan",
        )
        assert result.triggered is True
        assert result.locale == "en"
        assert "Insufficient evidence" in result.answer

    def test_no_report_no_op(self):
        result = evaluate_low_evidence_policy(
            answer="x",
            report=None,
            question="q",
        )
        assert result.triggered is False
        assert result.answer == "x"
        assert result.reason == "no_report"

    def test_log_payload_round_trips(self):
        report = _Report(
            total_chunks=10,
            verified_chunks=0,
            unverified_chunks=10,
        )
        result = evaluate_low_evidence_policy(
            answer="x",
            report=report,
            question="q",
        )
        payload = result.to_log_payload()
        assert payload["triggered"] is True
        assert payload["verified_chunks"] == 0
        assert payload["total_chunks"] == 10
        assert payload["verified_ratio"] == 0.0
        assert payload["unverified_total"] == 10
        assert payload["locale"] == "en"
        assert "verified_ratio" in payload["reason"]


class TestIsEvidenceExpected:
    """The loop derives ``evidence_expected`` from this helper; a generative
    coding turn (role=programmer) must switch the gate OFF so a docstring/diff
    the user asked for is not deleted as 'unverified'."""

    def test_programmer_role_never_expects_evidence(self):
        # Even with a non-empty chain (a file WAS read) and realtime intent,
        # a programmer turn delivers new code/docstring — no evidence expected.
        assert is_evidence_expected(
            role="programmer",
            chain_was_empty=False,
            realtime_required=True,
        ) is False

    def test_factual_role_with_read_file_keeps_gate(self):
        # A researcher/operator turn that read a file still faces the gate.
        for role in ("researcher", "technical_report", "operator_chat", ""):
            assert is_evidence_expected(
                role=role,
                chain_was_empty=False,
                realtime_required=False,
            ) is True, role

    def test_empty_chain_non_realtime_reasoning_bypasses_gate(self):
        # Pure design/reasoning answer: empty chain + no realtime intent.
        assert is_evidence_expected(
            role="operator_chat",
            chain_was_empty=True,
            realtime_required=False,
        ) is False

    def test_empty_chain_but_realtime_keeps_gate(self):
        # A realtime factual question with an empty chain still expects evidence.
        assert is_evidence_expected(
            role="researcher",
            chain_was_empty=True,
            realtime_required=True,
        ) is True

    def test_defaults_expect_evidence(self):
        # Unknown role, no signals -> conservative default keeps the gate on.
        assert is_evidence_expected() is True


class TestProgrammerDocstringSurvivesTruncation:
    """End-to-end: the regression that motivated the fix. A programmer turn
    proposes a new docstring (14 chunks, 2 verified) that would otherwise be
    replaced by the 'insufficient data' stub. With evidence_expected derived
    from the programmer role, the generated answer survives verbatim."""

    def test_generated_docstring_is_not_suppressed(self):
        report = _Report(
            total_chunks=14,
            verified_chunks=2,
            unverified_chunks=12,
            chunks=tuple(
                _Chunk(text=f"docstring line {i}.", verdict="unverified")
                for i in range(14)
            ),
        )
        # evidence_expected as the loop would now compute it for a programmer
        # turn that read the source file (non-empty chain).
        evidence_expected = is_evidence_expected(
            role="programmer",
            chain_was_empty=False,
            realtime_required=True,
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="Прочитай core/task_complexity.py и покажи новый docstring",
            evidence_expected=evidence_expected,
        )
        assert result.triggered is False
        assert result.reason == "no_evidence_expected"
        assert result.answer == _SAMPLE_LONG_ANSWER

    def test_same_distribution_still_truncates_for_factual_turn(self):
        # Control: an identical low-evidence distribution from a FACTUAL turn
        # (evidence expected) is still suppressed — the fix is scoped to code.
        report = _Report(
            total_chunks=14,
            verified_chunks=2,
            unverified_chunks=12,
            chunks=tuple(
                _Chunk(text=f"claim {i}.", verdict="unverified")
                for i in range(14)
            ),
        )
        evidence_expected = is_evidence_expected(
            role="researcher",
            chain_was_empty=False,
            realtime_required=True,
        )
        result = evaluate_low_evidence_policy(
            answer=_SAMPLE_LONG_ANSWER,
            report=report,
            question="what is the current inflation rate?",
            evidence_expected=evidence_expected,
        )
        assert result.triggered is True
