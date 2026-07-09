"""Lightweight, read-only self-build supervisor cycle.

The supervisor decides — in one short pass — whether the agent should *wait*,
*stop*, or *propose* exactly one evidence-backed self-build candidate. It never
applies changes, never writes files, never runs tests, never calls shell_exec,
and never refreshes models or provider catalogs. All inputs are gathered by the
caller from existing local operator helpers; this module holds only the pure
decision logic so it is trivially testable with no real LLM/provider/network.

Decision order (first gate that trips wins):
1. budget      -> status="budget_wait"   when the hour window is near exhaustion
2. approvals   -> status="approval_wait"  when approval items are pending
3. scheduler / task queue / recent errors / TECH_DEBT.md  (informational)
4. candidate   -> status="ok" with one candidate payload or "NO_PATCH"

The candidate is produced lazily through ``candidate_provider`` and is only
invoked once budget and approval gates have passed, so a waiting cycle never
runs the heavier candidate analysis.
"""
from __future__ import annotations

from typing import Any, Callable

# Counters in the persistent hour window that gate a new self-build cycle.
_GATED_WINDOW = "hour"

# Default safety margins. A cycle waits when fewer than this many hourly LLM
# calls remain, or when the hourly model-token headroom drops to/below this
# fraction of the configured limit.
_DEFAULT_LLM_CALLS_MIN = 3
_DEFAULT_TOKEN_RATIO_MIN = 0.1


def _window_by_name(budget_windows: Any, name: str) -> dict | None:
    if not isinstance(budget_windows, dict):
        return None
    for window in budget_windows.get("windows") or []:
        if isinstance(window, dict) and window.get("name") == name:
            return window
    return None


def hour_budget_headroom(budget_windows: Any) -> dict[str, dict[str, float]]:
    """Per-counter headroom for the persistent hour window.

    Only *configured* counters (limit > 0) are returned; a counter with limit 0
    is unenforced and never gates a cycle. Each entry carries used, limit,
    headroom (limit-used, floored at 0) and ratio_left (headroom/limit).
    """
    window = _window_by_name(budget_windows, _GATED_WINDOW)
    result: dict[str, dict[str, float]] = {}
    if window is None:
        return result
    for name, data in (window.get("counters") or {}).items():
        if not isinstance(data, dict):
            continue
        limit = int(data.get("limit", 0) or 0)
        if limit <= 0:
            continue
        used = int(data.get("used", 0) or 0)
        headroom = max(0, limit - used)
        result[name] = {
            "used": used,
            "limit": limit,
            "headroom": headroom,
            "ratio_left": headroom / limit,
        }
    return result


def is_budget_near_exhaustion(
    headroom: dict[str, dict[str, float]],
    *,
    llm_calls_min: int = _DEFAULT_LLM_CALLS_MIN,
    token_ratio_min: float = _DEFAULT_TOKEN_RATIO_MIN,
) -> tuple[bool, list[str]]:
    """True (with human-readable reasons) when the hour window is too tight to
    safely start an LLM-capable self-build cycle."""
    reasons: list[str] = []
    llm = headroom.get("llm_calls")
    if llm is not None and llm["headroom"] <= llm_calls_min:
        reasons.append(
            f"hour llm_calls headroom {int(llm['headroom'])}/{int(llm['limit'])} "
            f"<= {llm_calls_min}"
        )
    tokens = headroom.get("model_tokens")
    if tokens is not None and tokens["ratio_left"] <= token_ratio_min:
        reasons.append(
            f"hour model_tokens headroom {int(tokens['headroom'])}/"
            f"{int(tokens['limit'])} (<= {token_ratio_min:.0%} left)"
        )
    return (bool(reasons), reasons)


def evaluate_self_build_supervisor(
    *,
    budget_windows: Any,
    approvals_pending: int,
    task_queue: dict | None = None,
    scheduler: dict | None = None,
    recent_errors: list | None = None,
    tech_debt: dict | None = None,
    candidate_provider: Callable[[], dict] | None = None,
    llm_calls_min: int = _DEFAULT_LLM_CALLS_MIN,
    token_ratio_min: float = _DEFAULT_TOKEN_RATIO_MIN,
) -> dict[str, Any]:
    """Run one supervisor decision. Returns a structured, log-safe report.

    ``candidate_provider`` is invoked at most once, and only when the budget and
    approval gates have passed, so a waiting cycle performs no candidate
    analysis and proposes nothing.
    """
    checked: list[str] = []
    evidence: dict[str, Any] = {}

    # 1. budget window ---------------------------------------------------------
    checked.append("budget")
    headroom = hour_budget_headroom(budget_windows)
    evidence["hour_budget_headroom"] = headroom
    near, budget_reasons = is_budget_near_exhaustion(
        headroom, llm_calls_min=llm_calls_min, token_ratio_min=token_ratio_min
    )
    if near:
        return {
            "status": "budget_wait",
            "reason": "; ".join(budget_reasons),
            "checked_sections": checked,
            "candidate": None,
            "evidence": evidence,
            "recommended_next_action": (
                "Wait for the hour budget window to refill, then re-run "
                ":self-build-supervisor."
            ),
        }

    # 2. pending approvals -----------------------------------------------------
    checked.append("approvals")
    pending = int(approvals_pending or 0)
    evidence["approvals_pending"] = pending
    if pending > 0:
        return {
            "status": "approval_wait",
            "reason": f"{pending} approval item(s) pending",
            "checked_sections": checked,
            "candidate": None,
            "evidence": evidence,
            "recommended_next_action": (
                "Clear the queue with :approval-list / :approval-triage before "
                "proposing new self-build work."
            ),
        }

    # 3. scheduler / task queue (informational) --------------------------------
    checked.append("scheduler")
    evidence["scheduler_due"] = int((scheduler or {}).get("due", 0) or 0)
    checked.append("task_queue")
    evidence["task_queue_pending_due"] = int(
        (task_queue or {}).get("pending_due", 0) or 0
    )

    # 4. recent errors (informational) -----------------------------------------
    checked.append("recent_errors")
    errors = list(recent_errors or [])
    evidence["recent_error_count"] = len(errors)
    if errors:
        evidence["recent_error_sample"] = errors[:5]

    # 5. tech debt (informational) ---------------------------------------------
    checked.append("tech_debt")
    evidence["tech_debt"] = dict(tech_debt or {})

    # 6. one candidate ---------------------------------------------------------
    checked.append("candidate")
    candidate = candidate_provider() if candidate_provider is not None else None
    if not candidate or candidate.get("diff") == "NO_PATCH":
        return {
            "status": "ok",
            "reason": "checks passed; no evidence-backed candidate available now",
            "checked_sections": checked,
            "candidate": "NO_PATCH",
            "evidence": evidence,
            "recommended_next_action": (
                "No safe minimal candidate right now; re-run later or inspect "
                "TECH_DEBT.md manually."
            ),
        }
    return {
        "status": "ok",
        "reason": "checks passed; one evidence-backed candidate selected",
        "checked_sections": checked,
        "candidate": candidate,
        "evidence": evidence,
        "recommended_next_action": (
            f"Review the candidate for {candidate.get('file')} and, if approved, "
            "implement it surgically (no auto-apply)."
        ),
    }
