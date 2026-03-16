"""
Learning rules safety: prevent rule explosion (ARCHITECTURE_PLAN_FULL.md).

rule_derivation must add rules only via add_rule(); limits: RULE_LIMIT,
rule_score, rule_decay. See learning/rule_derivation.py.
"""
from __future__ import annotations

import time
from typing import Any

# Limits (can be moved to config later)
RULE_LIMIT = 200
RULE_DECAY_AGE_SECONDS = 86400 * 30  # 30 days — rules older can be deprioritized/trimmed
_DEFAULT_SCORE = 1.0

_rules: list[dict[str, Any]] = []  # { "rule": str, "score": float, "created_at": float }


def add_rule(rule: str, score: float = _DEFAULT_SCORE) -> bool:
    """
    Add a derived rule. Enforces rule_limit; may trim low-score or old rules (decay).
    Returns True if added, False if rejected (e.g. at limit and rule not better than worst).
    """
    global _rules
    _apply_decay()
    if len(_rules) >= RULE_LIMIT:
        # Remove lowest-score or oldest to make room
        _rules.sort(key=lambda x: (x.get("score", 0), -x.get("created_at", 0)))
        if score <= (_rules[0].get("score") or 0):
            return False
        _rules.pop(0)
    _rules.append({
        "rule": rule[:2000],
        "score": score,
        "created_at": time.time(),
    })
    return True


def _apply_decay() -> None:
    """Optional: decay score for very old rules so they can be trimmed first."""
    now = time.time()
    for r in _rules:
        age = now - r.get("created_at", 0)
        if age > RULE_DECAY_AGE_SECONDS:
            r["score"] = max(0.0, (r.get("score") or 1.0) * 0.5)


def get_rules(limit: int = 100, min_score: float | None = None) -> list[dict[str, Any]]:
    """Return rules, optionally filtered by min_score, sorted by score desc."""
    _apply_decay()
    out = list(_rules)
    if min_score is not None:
        out = [r for r in out if (r.get("score") or 0) >= min_score]
    out.sort(key=lambda x: -(x.get("score") or 0))
    return out[:limit]


def rule_count() -> int:
    return len(_rules)


def clear_rules() -> None:
    """For tests or reset."""
    global _rules
    _rules = []
