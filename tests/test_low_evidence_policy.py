"""Low-evidence answer policy.

Triggers a deterministic truncation of the user-facing answer when the
verifier's verdict distribution is severely below threshold. Pairs
with `core.evidence_support` (which is observation-only)
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


    def test_no_report_no_op(self):
        result = evaluate_low_evidence_policy(
            answer="x",
            report=None,
            question="q",
        )
        assert result.triggered is False
        assert result.answer == "x"
        assert result.reason == "no_report"


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


class TestGenerativeExemptionIsScopedToGeneratedOutput:
    """The programmer exemption keyed on WHO answered, not on WHAT was produced.

    Measured live on the operator's environment: "In one sentence: what does
    core/model_router.py do?" read the file, produced 7 chunks of which 6 were
    verified, and still logged
    `evidence_support applicable=False reason=no_evidence_expected`.
    With `chain_was_empty=False` the second branch of `is_evidence_expected`
    can only return True, so the exemption came from `role="programmer"` alone.

    Same class as the case already recorded in docs/COGNITIVE_CORE.md (open
    question 3): "how many TODO/FIXME are in core/?" ran under role=programmer
    and skipped evidence checking for a counting question.

    The exemption exists because generated code "can never appear verbatim in
    the source file it was derived from". That rationale reaches exactly as far
    as answers that actually carry a generated artifact.
    """


    def test_a_fenced_code_answer_under_the_programmer_role_owes_nothing(self):
        # Guard: the case the exemption was written for must keep it.
        answer = (
            "Here is the helper you asked for:\n\n"
            "```python\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "```\n"
        )
        assert is_evidence_expected(
            role="programmer",
            chain_was_empty=False,
            realtime_required=False,
            answer=answer,
        ) is False

    def test_a_diff_answer_under_the_programmer_role_owes_nothing(self):
        # Guard: a unified diff is a generated artifact even without a fence.
        answer = (
            "Apply this patch:\n"
            "--- a/core/x.py\n"
            "+++ b/core/x.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
        )
        assert is_evidence_expected(
            role="programmer",
            chain_was_empty=False,
            realtime_required=False,
            answer=answer,
        ) is False

    def test_the_exemption_is_unchanged_when_the_answer_is_not_shown(self):
        # Back-compat: a caller that does not hand over the answer gets exactly
        # the old behaviour, so no existing call site changes silently.
        assert is_evidence_expected(
            role="programmer",
            chain_was_empty=False,
            realtime_required=False,
        ) is False


def test_a_tool_name_is_not_a_fact_about_the_world():
    """Рабочий экзамен 2026-09-19, пятый прогон: оценка «4 шага … подсчёт сделает
    python_probe» стёрта — «python» внутри имени инструмента счёлся внешним фактом."""
    from core.low_evidence_policy import _states_external_fact, is_evidence_expected

    estimate = "ОЦЕНКА: 4\nПотом подсчёт уникальных IP сделает python_probe по трём файлам."
    assert not is_evidence_expected(role="operator_chat", answer=estimate)
    assert _states_external_fact("Python 3.13 вышел 7 октября 2024 года.")


def test_a_restated_user_figure_and_a_self_fact_are_not_world_claims():
    """Рабочий экзамен 2026-09-19, шестой прогон: разбор заказов стёрт — ответ
    пересказывал «сделка на бирже на $500» из вопроса и говорил «Python
    установлен (3.11.9) [runtime:python_version]»."""
    from core.low_evidence_policy import is_evidence_expected

    question = "12. Совершить сделку на бирже на $500 по сигналу заказчика"
    triage = ("12: НЕ БЕРУ — сделка на бирже на $500 требует денег заказчика.\n"
              "Python установлен (версия 3.11.9) [runtime:python_version].\n1: БЕРУ — CSV.")
    assert not is_evidence_expected(role="operator_chat", answer=triage, question=question)
    assert is_evidence_expected(role="operator_chat", answer=triage.replace("$500", "$900"),
                                question=question), "своя сумма в долларах — по-прежнему факт"
