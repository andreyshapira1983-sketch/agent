"""Tests for claim-level answer enforcement (critique plan PR3)."""
from __future__ import annotations

from dataclasses import dataclass

from core.low_evidence_policy import evaluate_low_evidence_policy
from core.unsupported_claims import (
    FEATURE_FLAG,
    FEATURE_FLAG_DEFAULT,
    apply_answer_enforcement,
    enforce_unsupported_claims_mode,
)


@dataclass
class _Chunk:
    text: str
    verdict: str
    matched_evidence_ids: tuple = ()


@dataclass
class _Report:
    total_chunks: int = 0
    verified_chunks: int = 0
    unverified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    topic_supported_but_claim_unverified_chunks: int = 0
    chunks: tuple = ()
    chain_was_empty: bool = False
    malformed_output: bool = False


_LONG = (
    "Conclusion: 30-day plan.\nFacts:\n- a\nSources: s\n"
    "Confidence: high\nUnverified: nothing\nSafety: ok"
)


def test_feature_flag_defaults_off(monkeypatch):
    assert FEATURE_FLAG == "enforce_unsupported_world_claims"
    assert FEATURE_FLAG_DEFAULT is False
    monkeypatch.delenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", raising=False)
    assert enforce_unsupported_claims_mode() == "off"


def test_mode_shadow_and_on(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "shadow")
    assert enforce_unsupported_claims_mode() == "shadow"
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "on")
    assert enforce_unsupported_claims_mode() == "on"


def test_local_critique_skips_empty_rewrite_even_when_long_zero_verified():
    report = _Report(
        total_chunks=10,
        verified_chunks=0,
        unverified_chunks=10,
        chunks=tuple(
            _Chunk(text=f"junk {i} definitely.", verdict="unverified")
            for i in range(10)
        ),
    )
    draft = "Conclusion: critique body kept.\nFacts:\n- weakness A [user:target]\n"
    result = apply_answer_enforcement(
        answer=draft,
        report=report,
        question="покажи слабые стороны",
        evidence_expected=True,
        local_critique_active=True,
        mode="on",
    )
    assert result.outcome == "local_critique_preserved"
    assert result.applied is False
    assert "critique body kept" in result.answer
    assert "no verified claim" not in result.answer.lower()
    assert "нет утверждений" not in result.answer.lower()
    # Would have truncated without the skip:
    le = evaluate_low_evidence_policy(
        answer=draft, report=report, question="q", evidence_expected=True
    )
    assert le.triggered is True
    assert result.would_change_answer is True


def test_verifier_failure_soft_fail_keeps_draft(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "on")
    draft = "Conclusion: keep me.\nFacts:\n- x\nConfidence: medium\nUnverified: nothing\nSafety: ok"
    result = apply_answer_enforcement(
        answer=draft,
        report=None,
        question="q",
        verifier_failure=True,
        mode="on",
    )
    assert result.outcome == "verifier_failure"
    assert "keep me" in result.answer
    assert "insufficient" not in result.answer.lower()
    assert "недостаточно данных" not in result.answer.lower()
    assert result.applied is True  # soft-fail note appended


def test_verifier_failure_shadow_logs_but_keeps_text(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "shadow")
    draft = "Conclusion: keep me.\nFacts:\n- x\nConfidence: medium\n"
    result = apply_answer_enforcement(
        answer=draft,
        report=None,
        verifier_failure=True,
        mode="shadow",
    )
    assert result.outcome == "verifier_failure"
    assert result.applied is False
    assert result.answer == draft
    assert result.would_change_answer is True


def test_malformed_report_soft_fail_on(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "on")
    report = _Report(malformed_output=True, total_chunks=3, unverified_chunks=3)
    draft = "loose prose without contract headers"
    result = apply_answer_enforcement(
        answer=draft,
        report=report,
        mode="on",
    )
    assert result.outcome == "malformed_report"
    assert "loose prose" in result.answer
    assert result.applied is True


def test_long_insufficient_evidence_still_truncates():
    report = _Report(
        total_chunks=10,
        verified_chunks=0,
        unverified_chunks=10,
        chunks=tuple(
            _Chunk(text=f"junk {i}.", verdict="unverified") for i in range(10)
        ),
    )
    result = apply_answer_enforcement(
        answer=_LONG,
        report=report,
        question="business plan",
        evidence_expected=True,
        local_critique_active=False,
        mode="off",
    )
    assert result.outcome == "insufficient_evidence"
    assert result.applied is True
    assert "no verified claim" in result.answer.lower()


def test_one_categorical_unverified_chunk_hedged_when_on(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "on")
    report = _Report(
        total_chunks=1,
        verified_chunks=0,
        unverified_chunks=1,
        chunks=(
            _Chunk(
                text="This is definitely the best market forever. [general-knowledge]",
                verdict="unverified",
            ),
        ),
    )
    draft = (
        "Conclusion: This is definitely the best market forever. [general-knowledge]\n"
        "Facts:\n- guaranteed growth [general-knowledge]\n"
        "Sources: gk\nConfidence: high\nUnverified: nothing\nSafety: ok"
    )
    result = apply_answer_enforcement(
        answer=draft,
        report=report,
        question="market?",
        evidence_expected=True,
        mode="on",
    )
    assert result.outcome == "unsupported_world_claims"
    assert result.applied is True
    assert "Confidence: low" in result.answer
    assert "definitely the best market" in result.answer  # body kept
    assert "no verified claim" not in result.answer.lower()


def test_one_categorical_chunk_shadow_does_not_rewrite(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "shadow")
    report = _Report(
        total_chunks=1,
        verified_chunks=0,
        unverified_chunks=1,
        chunks=(
            _Chunk(text="definitely true forever.", verdict="unverified"),
        ),
    )
    draft = "Conclusion: definitely true forever.\nConfidence: high\n"
    result = apply_answer_enforcement(
        answer=draft,
        report=report,
        evidence_expected=True,
        mode="shadow",
    )
    assert result.outcome == "unsupported_world_claims"
    assert result.applied is False
    assert result.would_change_answer is True
    assert result.answer == draft


def test_short_non_categorical_not_forced():
    report = _Report(
        total_chunks=2,
        verified_chunks=0,
        unverified_chunks=2,
        chunks=(
            _Chunk(text="Maybe useful.", verdict="unverified"),
            _Chunk(text="Perhaps related.", verdict="unverified"),
        ),
    )
    draft = "Conclusion: Maybe useful.\nConfidence: low\n"
    result = apply_answer_enforcement(
        answer=draft,
        report=report,
        evidence_expected=True,
        mode="on",
    )
    assert result.outcome == "none"
    assert result.applied is False


def test_low_evidence_local_critique_param():
    report = _Report(
        total_chunks=10,
        verified_chunks=0,
        unverified_chunks=10,
    )
    result = evaluate_low_evidence_policy(
        answer=_LONG,
        report=report,
        question="q",
        local_critique_active=True,
    )
    assert result.triggered is False
    assert result.reason == "local_critique_skip_empty_rewrite"
