"""Tests for claim-level answer enforcement (critique plan PR3)."""
from __future__ import annotations

from dataclasses import dataclass

from core.unsupported_claims import (
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


def test_mode_shadow_and_on(monkeypatch):
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "shadow")
    assert enforce_unsupported_claims_mode() == "shadow"
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "on")
    assert enforce_unsupported_claims_mode() == "on"


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
