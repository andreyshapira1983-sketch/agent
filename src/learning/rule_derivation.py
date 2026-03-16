"""
Rule derivation: derive rules from feedback/experience and add only via rules_safety.
Prevents rule explosion — all new rules go through add_rule (limit, score, decay).
"""
from __future__ import annotations

from src.learning import rules_safety


def add_derived_rule(rule: str, score: float = 1.0) -> bool:
    """
    Add a derived rule. Enforces rule_limit and rule_decay.
    Returns True if added, False if rejected (at limit and not better than worst).
    """
    return rules_safety.add_rule(rule, score)


def derive_rules_from_feedback(max_rules: int = 5) -> list[str]:
    """
    Derive simple rules from recent low-rated feedback (e.g. "avoid X when Y").
    Each derived rule is added via rules_safety.add_rule. Returns list of added rules.
    """
    from src.learning.feedback_analyzer import get_summary
    from src.learning.feedback import get_recent_feedback

    get_summary(20)  # ensure feedback summary is available
    low_rated = [
        (i.get("request", ""), i.get("response", ""), i.get("rating"))
        for i in get_recent_feedback(20)
        if i.get("rating") is not None and (i.get("rating") or 0) < 0.6
    ]
    added: list[str] = []
    for req, resp, rating in low_rated[:max_rules]:
        rule = f"When user says something like «{req[:80]}», avoid responses like «{resp[:80]}» (rating was {rating})."
        if rules_safety.add_rule(rule, score=0.7):
            added.append(rule)
    return added


def get_derived_rules(limit: int = 50, min_score: float | None = None) -> list[dict]:
    """Read derived rules (from rules_safety)."""
    return rules_safety.get_rules(limit=limit, min_score=min_score)


def rule_count() -> int:
    return rules_safety.rule_count()
