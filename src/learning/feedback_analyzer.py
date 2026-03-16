"""
Feedback analyzer: scoring, summary of recent feedback and errors for self-improvement.
"""
from __future__ import annotations

from typing import Any, cast


def get_summary(n: int = 20) -> dict[str, Any]:
    """
    Analyze last n feedback entries: count by rating, summarize low ratings (errors),
    return text summary for the agent.
    """
    from src.learning.feedback import get_recent_feedback
    from src.monitoring.metrics import get_metrics

    items = get_recent_feedback(n)
    metrics = get_metrics()

    def _rating(i: dict[str, Any]) -> float:
        r = i.get("rating")
        return float(r) if r is not None else 0.0

    good = sum(1 for i in items if _rating(i) >= 0.6)
    bad = sum(1 for i in items if i.get("rating") is not None and _rating(i) < 0.6)
    unrated = sum(1 for i in items if i.get("rating") is None)

    errors_total = metrics.get("errors", 0)
    recent_with_rating = [i for i in items if i.get("rating") is not None]
    low_rated = [i for i in recent_with_rating if _rating(i) < 0.6]

    summary_lines = [
        f"Feedback (last {len(items)}): good={good}, low_rated={bad}, unrated={unrated}.",
        f"Total errors (metrics): {errors_total}.",
    ]
    if low_rated:
        summary_lines.append("Low-rated examples (learn from these):")
        for i in low_rated[:5]:
            summary_lines.append(f"  - «{str(i.get('request', ''))[:60]}» → rating {i.get('rating')}")
    return {
        "good_count": good,
        "bad_count": bad,
        "unrated_count": unrated,
        "errors_total": errors_total,
        "summary_text": "\n".join(summary_lines),
    }


def get_summary_text(n: int = 20) -> str:
    """Plain text summary for injection into context or tool result."""
    return cast(str, get_summary(n)["summary_text"])
