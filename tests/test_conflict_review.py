"""Operator-facing conflict review tests."""

from __future__ import annotations

from core.conflict_review import ConflictReview
from core.source_registry import SourceRegistry


def test_conflict_review_suggests_stronger_claim():
    registry = SourceRegistry()
    strong = registry.register_source(
        type="documentation",
        title="official docs",
        locator="docs.md",
        trust_level=0.90,
    )
    weak = registry.register_source(
        type="forum",
        title="forum thread",
        locator="forum",
        trust_level=0.35,
    )
    winner = registry.register_claim(
        source_id=strong.id,
        text="Agent mode is local.",
        confidence=0.92,
    )
    registry.register_claim(
        source_id=weak.id,
        text="Agent mode is cloud.",
        confidence=0.40,
    )

    report = ConflictReview().review(registry)

    assert report.conflict_count == 1
    suggestion = report.suggestions[0]
    assert suggestion.decision == "suggested"
    assert suggestion.winner_claim_id == winner.id
    assert suggestion.confidence >= 0.65
    assert suggestion.claims[0].claim_id == winner.id


def test_conflict_review_requires_human_when_scores_are_close():
    registry = SourceRegistry()
    left = registry.register_source(type="file", title="a", locator="a.txt", trust_level=0.80)
    right = registry.register_source(type="file", title="b", locator="b.txt", trust_level=0.78)
    registry.register_claim(source_id=left.id, text="Agent role is planner.", confidence=0.80)
    registry.register_claim(source_id=right.id, text="Agent role is executor.", confidence=0.79)

    report = ConflictReview().review(registry)

    assert report.conflict_count == 1
    suggestion = report.suggestions[0]
    assert suggestion.decision == "needs_review"
    assert suggestion.winner_claim_id is None
    assert "too close" in " ".join(suggestion.reasons)


def test_conflict_review_summary_and_dict_are_operator_readable():
    registry = SourceRegistry()
    a = registry.register_source(type="file", title="a", locator="a.txt", trust_level=0.90)
    b = registry.register_source(type="file", title="b", locator="b.txt", trust_level=0.50)
    registry.register_claim(source_id=a.id, text="Budget policy is strict.", confidence=0.90)
    registry.register_claim(source_id=b.id, text="Budget policy is loose.", confidence=0.50)

    report = ConflictReview().review(registry)
    payload = report.to_dict()
    summary = report.user_summary()

    assert payload["conflict_count"] == 1
    assert payload["suggestions"][0]["subject"] == "budget policy"
    assert "=== conflicts ===" in summary
    assert "budget policy" in summary
    assert "claim_" in summary
