"""Priority intelligence: choose the single most important next action.

A bounded-autonomy agent is only useful if it is *initiative inside a safe
corridor*: it must notice problems, connect signals, and surface the **one**
action that matters most right now — with evidence, a risk estimate, and an
honest account of what it does **not** know. It must NOT spray ten speculative
proposals every tick.

This module is the "what should I do next?" brain. It is intentionally
**pure and advisory**:

* it reads structured signals (passed in) and returns one recommendation;
* it performs no I/O, mutates nothing, and never executes the action;
* it always returns exactly one :class:`BestNextAction` — even "just observe" —
  so the agent is forced to commit to a single priority and justify it.

The selection is deterministic: every candidate carries a fixed priority score
and the highest score wins. Ties resolve by score then by a stable order, so the
same signals always yield the same advice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from core.approval_triage import TriageReport


Severity = Literal["critical", "high", "medium", "low", "none"]


# Priority scores. Higher wins. Chosen so the ordering is obvious and stable:
# if the daemon is not even ticking, nothing else can be trusted; a hard tick
# error outranks a test failure; a test failure outranks softer hygiene work.
_P_DAEMON_DOWN = 100      # heartbeat missing/stale: agent may not be running
_P_TICK_ERROR = 90        # last tick raised: the loop itself is broken
_P_TESTS_FAIL = 80        # concrete failing tests: minimal repair is provable
_P_TESTS_INCONCLUSIVE = 60  # timed-out/unknown: must not be read as healthy
_P_INBOX_DEBT = 50        # duplicate proposals accumulating into admin debt
_P_DRY_RUN_STUCK = 40     # many dry-run ticks: never applied anything, ask why
_P_INBOX_BACKLOG = 30     # large pending queue with no clear duplicates
_P_OBSERVE = 0            # nothing pressing: stay in honest observation


# Thresholds (deliberately conservative — advice, not automation).
_DRY_RUN_STREAK_ALERT = 5     # ~5 consecutive dry-run ticks before nudging
_INBOX_DEBT_DUPLICATES = 3    # this many duplicates is real debt, not noise
_INBOX_BACKLOG_PENDING = 12   # backlog worth a dedicated review pass


@dataclass(frozen=True)
class BestNextAction:
    action: str
    title: str
    severity: Severity
    priority: int
    reason: str
    evidence: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    risk: str = "read_only"
    recommended_command: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "title": self.title,
            "severity": self.severity,
            "priority": self.priority,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "unknowns": list(self.unknowns),
            "risk": self.risk,
            "recommended_command": self.recommended_command,
            "confidence": self.confidence,
        }


def select_best_next_action(
    *,
    result_status: str = "none",
    tests_health: str = "none",
    dry_run_streak: int = 0,
    heartbeat_missing: bool = False,
    heartbeat_stale: bool = False,
    heartbeat_age_seconds: Optional[float] = None,
    last_event: str = "",
    tick_error: Optional[str] = None,
    failed_tests: tuple[str, ...] = (),
    triage: Optional[TriageReport] = None,
    inbox_pending: int = 0,
) -> BestNextAction:
    """Pick the single most important next action from the current signals.

    Pure: no I/O, no mutation, no execution. The caller gathers the signals
    (typically from the latest heartbeat plus a triage pass) and decides what
    to do with the recommendation. The agent is expected to PROPOSE this action,
    not perform it.

    Returns exactly one :class:`BestNextAction`. When nothing is pressing it
    returns an honest ``observe`` action rather than inventing busywork.
    """
    candidates: list[BestNextAction] = []

    daemon = _candidate_daemon(heartbeat_missing, heartbeat_stale, heartbeat_age_seconds, last_event)
    if daemon is not None:
        candidates.append(daemon)

    err = _candidate_tick_error(tick_error)
    if err is not None:
        candidates.append(err)

    tests = _candidate_tests(tests_health, result_status, failed_tests)
    if tests is not None:
        candidates.append(tests)

    debt = _candidate_inbox_debt(triage)
    if debt is not None:
        candidates.append(debt)

    stuck = _candidate_dry_run_stuck(dry_run_streak)
    if stuck is not None:
        candidates.append(stuck)

    backlog = _candidate_inbox_backlog(triage, inbox_pending)
    if backlog is not None:
        candidates.append(backlog)

    if not candidates:
        return _candidate_observe(tests_health, result_status, inbox_pending)

    # Deterministic: highest priority wins; ties keep first-appended (which is
    # already the intended severity order above).
    return max(candidates, key=lambda c: c.priority)


# ── candidate generators ─────────────────────────────────────────────────────

def _candidate_daemon(
    missing: bool,
    stale: bool,
    age_seconds: Optional[float],
    last_event: str,
) -> Optional[BestNextAction]:
    if not (missing or stale):
        return None
    if missing:
        evidence = ("no heartbeat file recorded — the daemon has never ticked",)
        reason = "The autonomous daemon has no heartbeat: it may not be running at all."
    else:
        age_min = (age_seconds or 0) / 60.0
        evidence = (
            f"last heartbeat {age_min:.1f} min ago (event={last_event or '?'})",
            "age exceeds the staleness window for the expected tick interval",
        )
        reason = "The daemon heartbeat is stale: ticks are not landing on schedule."
    return BestNextAction(
        action="restore_daemon_liveness",
        title="Verify the autonomous daemon is actually running",
        severity="critical",
        priority=_P_DAEMON_DOWN,
        reason=reason,
        evidence=evidence,
        unknowns=(
            "whether the scheduler/process is alive or merely idle",
            "whether earlier ticks failed silently before the gap",
        ),
        risk="read_only",
        recommended_command="agent_tick.py --status",
        confidence=0.6 if stale and not missing else 0.5,
    )


def _candidate_tick_error(tick_error: Optional[str]) -> Optional[BestNextAction]:
    if not tick_error:
        return None
    return BestNextAction(
        action="investigate_tick_error",
        title="Investigate the last failed tick",
        severity="critical",
        priority=_P_TICK_ERROR,
        reason="The most recent tick raised an exception: the loop is broken, not merely idle.",
        evidence=(f"last tick ended with error: {tick_error[:200]}",),
        unknowns=(
            "whether the error is transient (env/network) or a real regression",
            "whether any partial side effects were applied before the failure",
        ),
        risk="read_only",
        recommended_command="logs/agent_tick.log (event=tick_error)",
        confidence=0.7,
    )


def _candidate_tests(
    tests_health: str,
    result_status: str,
    failed_tests: tuple[str, ...],
) -> Optional[BestNextAction]:
    if tests_health == "fail" or result_status == "failed":
        sample = ", ".join(failed_tests[:5]) if failed_tests else "see tick log"
        evidence = (
            f"tests_health={tests_health}, result_status={result_status}",
            f"failing test(s): {sample}",
        )
        return BestNextAction(
            action="propose_minimal_test_repair",
            title="Propose one minimal fix for the failing tests",
            severity="high",
            priority=_P_TESTS_FAIL,
            reason="Tests are failing with concrete names: this is the most provable, bounded fix available.",
            evidence=evidence,
            unknowns=(
                "the root cause until the failing assertion is read",
                "whether one patch covers all failures or only the first",
            ),
            risk="reversible",
            recommended_command=":propose-repair",
            confidence=0.65,
        )
    if tests_health == "inconclusive" or result_status == "inconclusive":
        return BestNextAction(
            action="resolve_inconclusive_tests",
            title="Re-establish a real test verdict (last run was inconclusive)",
            severity="high",
            priority=_P_TESTS_INCONCLUSIVE,
            reason="The last test run timed out or produced no verdict: health is currently unknown, not green.",
            evidence=(
                f"tests_health={tests_health}, result_status={result_status}",
                "a timed-out / exit-code-less run is not evidence of health",
            ),
            unknowns=(
                "whether the code is actually healthy — there is NO verdict yet",
                "whether the timeout was load-related or a hang/regression",
            ),
            risk="read_only",
            recommended_command="re-run the suite with a longer timeout",
            confidence=0.55,
        )
    return None


def _candidate_inbox_debt(triage: Optional[TriageReport]) -> Optional[BestNextAction]:
    if triage is None:
        return None
    dupes = len(triage.duplicates)
    if dupes < _INBOX_DEBT_DUPLICATES:
        return None
    top = triage.clusters[0] if triage.clusters else None
    top_desc = f"{top.label} ({top.count})" if top is not None else "n/a"
    return BestNextAction(
        action="reduce_inbox_duplicate_debt",
        title="Triage the approval inbox and dismiss duplicates",
        severity="medium",
        priority=_P_INBOX_DEBT,
        reason="Duplicate proposals are accumulating into administrative debt and drowning real signal.",
        evidence=(
            f"{dupes} structural duplicate(s) across {len(triage.clusters)} cluster(s)",
            f"largest cluster: {top_desc}",
        ),
        unknowns=(
            "whether any 'duplicate' is actually a distinct intent reworded",
        ),
        risk="read_only",
        recommended_command=":approval-triage",
        confidence=0.6,
    )


def _candidate_dry_run_stuck(dry_run_streak: int) -> Optional[BestNextAction]:
    try:
        streak = int(dry_run_streak)
    except (TypeError, ValueError):
        streak = 0
    if streak < _DRY_RUN_STREAK_ALERT:
        return None
    return BestNextAction(
        action="review_dry_run_stall",
        title="Decide whether the long dry-run streak should ever apply effects",
        severity="medium",
        priority=_P_DRY_RUN_STUCK,
        reason="The daemon has run dry for many ticks: it keeps proposing but never applies anything.",
        evidence=(
            f"dry_run_streak={streak} consecutive dry-run tick(s)",
            "no effects have been applied across this streak",
        ),
        unknowns=(
            "whether staying dry-run is intentional policy or a forgotten gate",
            "whether any proposed task is actually worth approving for real effect",
        ),
        risk="external",
        recommended_command=":approval-list  (review before enabling effects)",
        confidence=0.45,
    )


def _candidate_inbox_backlog(
    triage: Optional[TriageReport],
    inbox_pending: int,
) -> Optional[BestNextAction]:
    pending = triage.total_pending if triage is not None else int(inbox_pending or 0)
    if pending < _INBOX_BACKLOG_PENDING:
        return None
    return BestNextAction(
        action="review_inbox_backlog",
        title="Review the large pending approval backlog",
        severity="low",
        priority=_P_INBOX_BACKLOG,
        reason="The pending queue is large enough to warrant a dedicated review pass.",
        evidence=(f"{pending} pending approval item(s)",),
        unknowns=("which items are still relevant versus stale",),
        risk="read_only",
        recommended_command=":approval-triage",
        confidence=0.4,
    )


def _candidate_observe(
    tests_health: str,
    result_status: str,
    inbox_pending: int,
) -> BestNextAction:
    return BestNextAction(
        action="observe",
        title="No pressing action — stay in honest observation",
        severity="none",
        priority=_P_OBSERVE,
        reason="No failing tests, no errors, no inbox debt, and the daemon is live: nothing warrants action now.",
        evidence=(
            f"tests_health={tests_health}, result_status={result_status}",
            f"{int(inbox_pending or 0)} pending approval item(s)",
        ),
        unknowns=("whether a problem exists that no current signal exposes",),
        risk="read_only",
        recommended_command=None,
        confidence=0.5,
    )


def format_best_next_action(action: BestNextAction) -> str:
    """Render the recommendation as a compact operator-facing block. Read-only."""
    lines: list[str] = []
    lines.append(
        f"best next action: {action.action} "
        f"[severity={action.severity} priority={action.priority} "
        f"confidence={action.confidence:.0%}]"
    )
    lines.append(f"  what: {action.title}")
    lines.append(f"  why:  {action.reason}")
    if action.evidence:
        lines.append("  evidence:")
        for fact in action.evidence:
            lines.append(f"    - {fact}")
    if action.unknowns:
        lines.append("  I do NOT know:")
        for unknown in action.unknowns:
            lines.append(f"    - {unknown}")
    lines.append(f"  risk: {action.risk}")
    if action.recommended_command:
        lines.append(f"  suggested: {action.recommended_command}")
    lines.append("  note: advisory only — not executed without approval")
    return "\n".join(lines)
