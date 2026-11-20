"""Priority intelligence: choose the single most important next action.

Pure and deterministic: reads structured signals, always returns exactly one :class:`BestNextAction` (even "just observe"), performs no I/O and never executes it. Candidates carry fixed priority scores; the highest wins, ties keep a stable order.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from core.approval_triage import TriageReport
from core.best_next_action_helpers import (  # noqa: F401 -- re-exported
    _COMMAND_TOKEN_RE,
    _DOC_NAME_RE,
    _DRAFT_VERB_RE,
    _DRY_RUN_STREAK_ALERT,
    _ENGINEERING_CONTEXT_RE,
    _ENGINEERING_GOAL_RE,
    _GOAL_ID_RE,
    _GOAL_STOP_WORDS,
    _GOAL_WORD_RE,
    _INBOX_BACKLOG_PENDING,
    _INBOX_DEBT_DUPLICATES,
    _OUTSIDE_RE,
    _OWN_ISSUE_MIN_SHARED_WORDS,
    _P_CAUSAL_CLIMB,
    _P_CAUSAL_DISCRIMINATE,
    _P_CAUSAL_EXPERIMENT,
    _P_CHARTER_DOCUMENT,
    _P_DAEMON_DOWN,
    _P_DRY_RUN_STUCK,
    _P_ENGINEERING_TASK,
    _P_EXTERNAL_STUDY,
    _P_INBOX_BACKLOG,
    _P_INBOX_DEBT,
    _P_OBSERVE,
    _P_OWN_ISSUE_NAMED,
    _P_SELF_IMPROVEMENT_FAILURE,
    _P_SELF_IMPROVEMENT_FAILURE_FRESH,
    _P_SPEC_BIRTH,
    _P_TESTS_FAIL,
    _P_TESTS_INCONCLUSIVE,
    _P_TICK_ERROR,
    _PY_TARGET_RE,
    _STUDY_VERB_RE,
    _SUBJECT_TOKEN_RE,
    _SUPPRESSIBLE_ACTIONS,
    _SUPPRESSIBLE_SEVERITIES,
    Severity,
    _goal_names_issue,
    _is_engineering_goal,
    _named_target,
    doc_target_from_goal,
    is_suppressible_alert,
    resolve_goal_subject,
    unresolved_goal_targets,
)
from core.self_improvement_issues import (
    SelfImprovementIssue,
    suppress_generic_issue_duplicates,
)

# Priority scores, higher wins: a daemon that is not ticking outranks everything, a hard tick error
# outranks a test failure, a test failure outranks softer hygiene work.
                          # road charter -> backlog; outranks the document goal so a goal naming
                          # both builds, not writes.
# above the durable-issue habit (55), below health alarms (60+); below the doc goal (58): a request
# to write is more concrete than one to read.
                          # defects: the hands follow the head; ties with the engineering task,
                          # which is admitted first and wins.
# PERISHABLE signal: fresh own pain outranks the routine (59); ties with health at 60 resolve to
# health, admitted first.
                          # rung is the deepest, prove causes first; investigation outranks starting
                          # a new one; own defects before admin debt.
                        # only a born experiment can move the claim; weight chain 47 exp > 46 judge
                        # > 45 explain > 44 birth > 40 stuck.


# Thresholds (deliberately conservative: advice, not automation).

# Severities an operator may acknowledge away (see core.alert_ack); critical/high are never
# suppressible.

# Advisory alert actions (the medium/low candidates below) an operator may acknowledge.
# An explicit registry so the REPL can validate an ack from the action NAME alone; critical/high are
# absent.


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
    recommended_command: str | None = None
    # Object NAMED in the grounds of the decision, when the grounds name one.
    # `None` means the grounds named no object, not that it is unknown.
    target_path: str | None = None
    confidence: float = 0.0
    # HOW this action was selected, derived at the selection site: `no_candidate`, `sole_candidate`
    # (no selection occurred) or `priority_table` (two or more competed).
    # No `agent_deliberation` value: no agent-owned decision boundary exists.
    decided_by: str = "unrecorded"
    # WHERE the grounds came from, derived from the input the candidate read: `operator_goal`,
    # `observed_state` (live signals) or `retained_record` (durable state from earlier runs).
    grounds: str = "unrecorded"
    # How many candidates were still in the race when the winner was taken; a suppressed candidate
    # is not a competitor.
    candidates_considered: int = 0

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
            "decided_by": self.decided_by,
            "grounds": self.grounds,
            "candidates_considered": self.candidates_considered,
        }


# Draft verb plus a .md name in the goal; otherwise the goal is not a document goal.


# Study goal: a study verb plus the outside world; reading the outside world is legitimate campaign
# work.


def _candidate_external_study(goal: str) -> BestNextAction | None:
    text = str(goal or "")
    if not (_STUDY_VERB_RE.search(text) and _OUTSIDE_RE.search(text)):
        return None
    return BestNextAction(
        action="study_external_source",
        title="Study the external source the campaign goal names",
        severity="medium",
        priority=_P_EXTERNAL_STUDY,
        reason=(
            "The campaign goal itself asks to study the outside world; the "
            "outcome is a HYPOTHESIS in the claim store, never a truth. "
            "Search, then FETCH the best page with web_fetch: search snippets "
            "are not sources, and claims resting on them die at verification "
            "(measured 2026-08-16: insufficient_for_realtime on snippet-only runs)."
        ),
        evidence=(f"goal: {text[:200]}",),
        unknowns=("whether the reading yields a testable hypothesis",),
        risk="read_only",
        recommended_command=None,
        confidence=0.7,
    )


# Charter engineering goal: words about backlog/split/failing test/gap.
# Structural half: a goal that NAMES a .py file and speaks of an engineering action on it is
# engineering whatever its wording.


# A path-like token has at least one directory separator; a bare word is not a path.
# Second subject vocabulary: a command name (`:team-run`) when the goal names no path-like token.


def _candidate_engineering_task(goal: str) -> BestNextAction | None:
    """Road from charter to backlog: a goal asking for engineering work births a self-build/Stage A request (language and wiring, not rights)."""
    text = str(goal or "")
    if not _is_engineering_goal(text):
        return None
    return BestNextAction(
        action="propose_engineering_task",
        title="Turn a real backlog candidate into a reviewed proposal",
        severity="medium",
        priority=_P_ENGINEERING_TASK,
        reason=(
            "The campaign goal itself asks for engineering work; the product "
            "is an approval item a human blesses — the lane and its gates "
            "stay exactly as they are."
        ),
        evidence=(f"goal: {text[:200]}",),
        unknowns=("whether the producer finds a grounded candidate",),
        risk="reversible",
        recommended_command=None,
        confidence=0.7,
        target_path=_named_target(text),
    )


def _candidate_charter_document(goal: str) -> BestNextAction | None:
    target = doc_target_from_goal(goal)
    if not target:
        return None
    return BestNextAction(
        action="draft_doctrine_document",
        title=f"Draft {target.rsplit('/', 1)[-1]} as the campaign goal asks",
        severity="medium",
        priority=_P_CHARTER_DOCUMENT,
        reason=(
            "The campaign goal itself asks for a doctrine document; drafting "
            "it IS the work, not a distraction from repair."
        ),
        evidence=(f"target document: {target}", f"goal: {goal[:200]}"),
        unknowns=("whether the draft survives human review",),
        risk="read_only",
        recommended_command=None,
        confidence=0.7,
    )


# Goal words by which it may name a defect-registry record: at least four letters and not a stop
# word.
# Threshold: three DIFFERENT content words of the title or a literal match of
# fingerprint/action/file.


# A concrete thing named by the goal: a record/episode/run identifier (a word with underscore or
# hyphen and a tail of six or more hex characters).
# File paths are resolved by `resolve_goal_subject`.


def _habit_shadowed_by_goal(
    goal: str,
    goal_subject: str | None,
    improvement: BestNextAction | None,
    issues: tuple[dict, ...],
) -> bool:
    """Whether the registry habit is set aside under this goal.

    Yes when the goal named another registry record, or a concrete identifier the record text lacks, and did not name the record's own subject (file). No when there is no goal or it is general.
    """
    text = str(goal or "")
    if improvement is None or not text.strip():
        return False
    if goal_subject and improvement.target_path == goal_subject:
        return False
    own = [SelfImprovementIssue.from_dict(raw).to_dict() for raw in issues]
    mine = [i for i in own if str(i.get("action")) == improvement.action]
    if any(_goal_names_issue(text, i) for i in mine):
        return False
    if any(_goal_names_issue(text, i) for i in own if i not in mine):
        return True
    ids = {m.group(0).lower() for m in _GOAL_ID_RE.finditer(text)}
    if not ids:
        return False
    known = " ".join(
        str(x) for i in mine for x in (i.get("title"), i.get("fingerprint"), *(i.get("evidence") or ()))
    ).lower()
    return any(token not in known for token in ids)


def _candidate_own_issue_named_by_goal(
    goal: str, issues: tuple[dict, ...],
) -> BestNextAction | None:
    """A goal naming an own defect gets the hands of THAT defect.

    Goal generators (engineering task, document, outside reading) gave zero candidates for "investigate/fix own defect", so the hands took the newest foreign record. The grounds are in the goal (operator_goal), so subject narrowing does not set it aside.
    """
    for raw in issues:
        issue = SelfImprovementIssue.from_dict(raw).to_dict()
        if str(issue.get("status") or "open") == "resolved":
            continue
        if not _goal_names_issue(goal, issue):
            continue
        files = [str(f) for f in issue.get("related_files") or () if str(f)]
        # The subject becomes the file the GOAL named, not the first in the list.
        named_file = next((f for f in files if f.lower() in str(goal).lower()), None)
        evidence = [f"goal names durable issue {issue.get('fingerprint')}"]
        evidence.extend(str(e)[:300] for e in (issue.get("evidence") or ())[:3])
        return BestNextAction(
            action=str(issue.get("action") or "improve_failure_to_idea_pipeline"),
            title=str(issue.get("title") or "Work the own defect the goal names"),
            severity="medium",
            priority=_P_OWN_ISSUE_NAMED,
            reason=(
                "The campaign goal names this defect of the agent's own; the "
                "hands follow the head instead of the newest record."
            ),
            evidence=tuple(evidence[:5]),
            unknowns=("whether the issue still reproduces until matching verification is recorded",),
            risk="read_only",
            recommended_command=str(issue.get("suggested_next_action") or "") or None,
            target_path=named_file or (files[0] if files else None),
            confidence=0.8,
        )
    return None


def _candidate_goal_names_missing(
    goal: str,
    goal_subject: str | None,
    goal_names_missing: tuple[str, ...] | None,
    active: list[BestNextAction],
) -> BestNextAction | None:
    """A named refusal when the goal calls for something absent from the workspace.

    Silence would mean work on someone else's matter under this goal's name.
    """
    if goal_subject or not goal_names_missing:
        return None
    if _DRAFT_VERB_RE.search(str(goal or "")):
        return None  # the goal asks to CREATE a file, which is legitimate
    if [c for c in active if c.severity in ("critical", "high")]:
        return None  # a breakage always outranks a goal
    names = ", ".join(goal_names_missing[:3])
    return BestNextAction(
        action="observe",
        title="Observe: the goal names something the workspace does not have",
        severity="none",
        priority=_P_OBSERVE,
        reason=(
            f"Nothing admissible: the goal names {names}, which does not "
            f"exist in the workspace, so it binds no work."
        ),
        evidence=(
            f"named but missing: {names}",
            f"{len(active)} candidate(s) set aside as unrelated to the goal",
        ),
        unknowns=("whether the goal meant a different path, or one not yet created",),
        decided_by="no_candidate",
        grounds="operator_goal",
    )


# Grounds meaning "this came from a GOAL". There are two because a goal has two origins and
# confusing them lies in reports.
# `_partition_by_subject` exempts such a candidate from subject narrowing and `core/campaign.py`
# counts goal cycles by it.
# The exemption is kept for BOTH kinds on purpose: taking it away from a self-chosen goal is a
# separate decision.
GOAL_GROUNDS: frozenset[str] = frozenset({"operator_goal", "self_goal"})


def _partition_by_subject(
    active: list[BestNextAction], goal_subject: str,
) -> tuple[list[BestNextAction], list[BestNextAction], list[BestNextAction]]:
    """Split candidates into "by subject", "about another" and "subject unknown".

    The third class exists because "do not know what about" is not "know it is about another".
    """
    on: list[BestNextAction] = []
    off: list[BestNextAction] = []
    unknown: list[BestNextAction] = []
    for candidate in active:
        if (
            candidate.severity in ("critical", "high")
            or candidate.target_path == goal_subject
            or candidate.grounds in GOAL_GROUNDS  # both kinds of goal; see GOAL_GROUNDS
        ):
            on.append(candidate)
        elif candidate.target_path is None:
            unknown.append(candidate)
        else:
            off.append(candidate)
    return on, off, unknown


def select_best_next_action(  # noqa: PLR0913 — flat: depth 1, all 2 returns are guard clauses
    *,
    # The goal was chosen by the agent itself, not named by a human; see `GOAL_GROUNDS`.
    goal_is_self: bool = False,
    goal: str = "",
    # The artifact the goal is ABOUT is resolved by the CALLER (`resolve_goal_subject`), since
    # resolution needs the file system.
    # `None` means the goal named no subject, so it narrows nothing.
    goal_subject: str | None = None,
    # Paths the goal named that do NOT exist, resolved by the caller (`unresolved_goal_targets`), so
    # a failed resolution is distinguishable from no name.
    goal_names_missing: tuple[str, ...] | None = None,
    result_status: str = "none",
    tests_health: str = "none",
    dry_run_streak: int = 0,
    heartbeat_missing: bool = False,
    heartbeat_stale: bool = False,
    heartbeat_age_seconds: float | None = None,
    last_event: str = "",
    tick_error: str | None = None,
    failed_tests: tuple[str, ...] = (),
    triage: TriageReport | None = None,
    inbox_pending: int = 0,
    acknowledged: frozenset[str] = frozenset(),
    self_improvement_registry_available: bool = False,
    open_self_improvement_issues: tuple[dict, ...] = (),
    recent_self_improvement_failures: tuple[str, ...] = (),
    fresh_self_improvement_failure: bool = False,
    unexplained_observations_count: int = 0,
    birth_candidates: int = 0,
    exhausted_actions: frozenset[str] | set[str] | None = None,
    discriminable_claims_count: int = 0,
    experimentable_claims_count: int = 0,
) -> BestNextAction:
    """Pick the single most important next action from the current signals.

    ``acknowledged`` is the set of action names the operator accepted (see :mod:`core.alert_ack`).
    An acknowledged candidate leaves the race ONLY when its severity is suppressible (``medium``/``low``); suppressed alerts surface through the ``observe`` fallback.
    """
    candidates: list[BestNextAction] = []

    exhausted = frozenset(exhausted_actions or ())

    def admit(candidate: BestNextAction | None, grounds: str) -> None:
        """Tag the candidate with WHERE its grounds came from, at the one place where the input it read is visible.
        Derived, never assigned.
        """
        # A ceiling-saturated action is not admitted to the race: ceiling knowledge is born in the
        # campaign and passed by the collector.
        # Critical candidates are exempt by construction: `exhausted_actions` holds only campaign
        # subject names.
        if candidate is not None and candidate.action in exhausted:
            return
        if candidate is not None:
            candidates.append(replace(candidate, grounds=grounds))

    goal_grounds = "self_goal" if goal_is_self else "operator_goal"
    admit(_candidate_engineering_task(goal), goal_grounds)
    admit(_candidate_charter_document(goal), goal_grounds)
    admit(_candidate_external_study(goal), goal_grounds)
    admit(_candidate_own_issue_named_by_goal(goal, open_self_improvement_issues), goal_grounds)

    admit(
        _candidate_daemon(heartbeat_missing, heartbeat_stale,
                          heartbeat_age_seconds, last_event),
        "observed_state",
    )
    admit(_candidate_tick_error(tick_error), "observed_state")
    admit(_candidate_tests(tests_health, result_status, failed_tests),
          "observed_state")

    # Both branches read state from EARLIER runs (durable issue registry or failures rebuilt from
    # episodes): a different provenance from a live signal.
    improvement = _candidate_open_self_improvement_issue(
        open_self_improvement_issues, subject=goal_subject, exhausted=exhausted,
    )
    if improvement is None and not self_improvement_registry_available:
        improvement = _candidate_self_improvement_failure(
            recent_self_improvement_failures,
            fresh=fresh_self_improvement_failure,
        )
    elif fresh_self_improvement_failure:
        # A perishable signal (60) must race alongside a registry record and beat it by priority,
        # not be locked behind a non-empty registry.
        admit(_candidate_self_improvement_failure(
            recent_self_improvement_failures, fresh=True,
        ), "retained_record")
    # Negative side of the hands-follow-head rule: see `_habit_shadowed_by_goal`.
    habit_shadowed: list[BestNextAction] = []
    if _habit_shadowed_by_goal(goal, goal_subject, improvement,
                               open_self_improvement_issues):
        habit_shadowed.append(replace(improvement, grounds="retained_record"))
        improvement = None
    admit(improvement, "retained_record")

    admit(_candidate_inbox_debt(triage), "observed_state")
    # Observations of OWN failures not covered by explanations: state accumulated by earlier runs.
    admit(_candidate_experimentable_claim(experimentable_claims_count),
          "retained_record")
    admit(_candidate_discriminable_claim(discriminable_claims_count),
          "retained_record")
    admit(_candidate_unexplained_observation(unexplained_observations_count),
          "retained_record")
    admit(_candidate_spec_birth(birth_candidates), "retained_record")
    admit(_candidate_dry_run_stuck(dry_run_streak), "observed_state")
    admit(_candidate_inbox_backlog(triage, inbox_pending), "observed_state")

    # Partition: an acknowledged advisory alert (medium/low) leaves the race; a critical/high
    # candidate is NEVER suppressible.
    active = [c for c in candidates if not _is_suppressed(c, acknowledged)]
    suppressed = [c for c in candidates if _is_suppressed(c, acknowledged)]
    off_subject: list[BestNextAction] = []
    unknown_subject: list[BestNextAction] = []

    # The goal NARROWS what is admissible, not just adds a candidate; otherwise its influence was
    # zero.
    # An objective breakage (critical/high) is never narrowed away, or a goal would become a way to
    # look past failing tests.
    missing = _candidate_goal_names_missing(
        goal, goal_subject, goal_names_missing, active,
    )
    if missing is not None:
        return missing

    if goal_subject:
        active, off_subject, unknown_subject = _partition_by_subject(
            active, goal_subject,
        )
    unknown_subject.extend(habit_shadowed)

    if not active:
        fallback = _candidate_observe(
            tests_health, result_status, inbox_pending, suppressed=suppressed,
            off_subject=off_subject, unknown_subject=unknown_subject,
            goal_subject=goal_subject,
        )
        # The idle reason is a FACT about who caused it: if the GOAL emptied the race it is the
        # grounds, otherwise live signals.
        emptied_by_goal = bool(off_subject or unknown_subject)
        return replace(
            fallback,
            decided_by="no_candidate",
            candidates_considered=0,
            grounds=goal_grounds if emptied_by_goal else "observed_state",
        )

    # Deterministic: highest priority wins; ties keep first-appended (the intended severity order
    # above).
    winner = max(active, key=lambda c: c.priority)
    # Provenance is recorded HERE and derived, not asserted: one active candidate means no choice
    # was made, two or more means the `_P_*` numbers settled it.
    return replace(
        winner,
        decided_by="sole_candidate" if len(active) == 1 else "priority_table",
        candidates_considered=len(active),
    )


def _is_suppressed(
    candidate: BestNextAction,
    acknowledged: frozenset[str],
) -> bool:
    """Whether this candidate is acknowledged AND soft enough to suppress."""
    return (
        candidate.action in acknowledged
        and candidate.severity in _SUPPRESSIBLE_SEVERITIES
    )


# ── candidate generators ─────────────────────────────────────────────────────

def _candidate_daemon(
    missing: bool,
    stale: bool,
    age_seconds: float | None,
    last_event: str,
) -> BestNextAction | None:
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


def _candidate_tick_error(tick_error: str | None) -> BestNextAction | None:
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
) -> BestNextAction | None:
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


def _candidate_inbox_debt(triage: TriageReport | None) -> BestNextAction | None:
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


def _candidate_self_improvement_failure(
    failures: tuple[str, ...],
    *,
    fresh: bool = False,
) -> BestNextAction | None:
    evidence = tuple(str(item).strip()[:300] for item in failures if str(item).strip())
    if not evidence:
        return None
    combined = " ".join(evidence).casefold()
    duplicate_mixin = "duplicate base class" in combined or (
        "duplicate" in combined and "mixin" in combined
    )
    if duplicate_mixin:
        action = "repair_incremental_splitter_duplicate_mixin"
        title = "Fix the incremental splitter duplicate mixin base class issue"
        if "too many lines" in combined:
            reason = (
                "A recent self-split rolled back on a duplicate mixin base class, "
                "and the follow-up repair proposal was rejected as too large."
            )
        else:
            reason = (
                "A recent self-split rolled back on a duplicate mixin base class; "
                "the failure remains actionable even though current health is clean."
            )
        command = "inspect core/incremental_splitter.py tests/test_incremental_splitter.py"
    else:
        action = "improve_failure_to_idea_pipeline"
        title = "Turn the recent self-improvement failure into a bounded repair"
        reason = (
            "Recent self-improvement history contains a rollback, rejection, or "
            "failure lesson that has not been followed by a successful apply."
        )
        if fresh:
            reason += (
                " The failure is younger than a day — a perishable signal whose "
                "trace and tree still agree — so it outranks routine backlog."
            )
        command = "review recent self-build history and propose one small read-only fix"
    return BestNextAction(
        action=action,
        title=title,
        severity="medium",
        priority=(_P_SELF_IMPROVEMENT_FAILURE_FRESH if fresh
                  else _P_SELF_IMPROVEMENT_FAILURE),
        reason=reason,
        evidence=evidence,
        unknowns=(
            "whether the recorded failure still reproduces without a focused read-only check",
        ),
        risk="read_only",
        recommended_command=command,
        confidence=0.7 if duplicate_mixin else 0.55,
    )


def _candidate_open_self_improvement_issue(
    issues: tuple[dict, ...],
    *,
    subject: str | None = None,
    exhausted: frozenset[str] = frozenset(),
) -> BestNextAction | None:
    dominant = list(suppress_generic_issue_duplicates(
        SelfImprovementIssue.from_dict(issue) for issue in issues
    ))
    if subject:
        # The goal's subject SELECTS a record rather than filtering the chosen one; the sort is
        # stable, so non-matching records keep their order.
        dominant.sort(
            key=lambda m: 0 if subject in (m.to_dict().get("related_files") or ()) else 1
        )
    for model in dominant:
        issue = model.to_dict()
        status = str(issue.get("status") or "open")
        if status == "resolved":
            continue
        # A case whose action is already exhausted in this run yields to the next, otherwise the
        # first record would shadow all others.
        if str(issue.get("action") or "improve_failure_to_idea_pipeline") in exhausted:
            continue
        fingerprint = str(issue.get("fingerprint") or "unknown")
        raw_evidence = issue.get("evidence") or ()
        # The class merge caps evidence at 8, so samples alone cannot say whether this fired 8 or 80
        # times; the count travels with the candidate.
        # A LOWER BOUND, not an exact count: records created before the counter existed arrive with
        # `occurrences=1`, so the `>=` sign says exactly this.
        seen = max(1, int(issue.get("occurrences") or 1))
        evidence = [f"durable issue {fingerprint} status={status} seen>={seen}x"]
        evidence.extend(str(item)[:300] for item in raw_evidence if str(item).strip())
        files = [str(item) for item in issue.get("related_files") or () if str(item)]
        if files:
            evidence.append("related files: " + ", ".join(files))
        # Severity is taken FROM the record, not nailed down here, so a goal's own defects can pass
        # `_partition_by_subject`.
        # The record vocabulary stops at `high`; `critical` never comes from a record. An unknown
        # word reads as `medium`, so old records keep their weight.
        severity = str(issue.get("severity") or "medium")
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        return BestNextAction(
            action=str(issue.get("action") or "improve_failure_to_idea_pipeline"),
            title=str(issue.get("title") or "Resolve the open self-improvement issue"),
            severity=severity,  # type: ignore[arg-type]
            priority=_P_SELF_IMPROVEMENT_FAILURE,
            reason=(
                "A durable self-improvement issue remains unresolved; unrelated "
                "successful applies cannot clear its lifecycle state."
            ),
            evidence=tuple(evidence[:6]),
            unknowns=(
                "whether the issue still reproduces until matching verification is recorded",
            ),
            risk="read_only",
            recommended_command=str(issue.get("suggested_next_action") or "") or None,
            # The subject named by the record itself is CARRIED, not left in the evidence text, so a
            # decision can be matched with the goal's subject.
            # The subject is the FILE matched on, not the first in the list: a record often names
            # several.
            target_path=(
                subject if subject and subject in files
                else (files[0] if files else None)
            ),
            confidence=0.75,
        )
    return None


def _candidate_experimentable_claim(count: int) -> BestNextAction | None:
    """Requests with intervention specs."""
    try:
        pending = int(count)
    except (TypeError, ValueError):
        pending = 0
    if pending < 1:
        return None
    return BestNextAction(
        action="run_claim_experiment",
        title="Prove a cause by removing it",
        severity="medium",
        priority=_P_CAUSAL_EXPERIMENT,
        reason=(
            "Open causal claims carry two-arm experiment specs; the "
            "intervention, not the author, proves the cause."
        ),
        evidence=(f"experimentable_claims={pending}",),
        risk="reversible",
    )


def _candidate_spec_birth(count: int) -> BestNextAction | None:
    """Requests marked "awaits experiment" without specs."""
    try:
        pending = int(count)
    except (TypeError, ValueError):
        pending = 0
    if pending < 1:
        return None
    return BestNextAction(
        action="birth_experiment_specs",
        title="Give a stuck claim its sandbox experiment",
        severity="medium",
        priority=_P_SPEC_BIRTH,
        reason=(
            "A judged-out claim waits for an experiment; only a born spec "
            "can move it, and the sandbox whitelist bounds what may run."
        ),
        evidence=(f"birth_candidates={pending}",),
        risk="reversible",
    )


def _candidate_discriminable_claim(count: int) -> BestNextAction | None:
    """Requests with live probes awaiting discrimination."""
    try:
        pending = int(count)
    except (TypeError, ValueError):
        pending = 0
    if pending < 1:
        return None
    return BestNextAction(
        action="discriminate_causal_claim",
        title="Let the journals judge the competing explanations",
        severity="medium",
        priority=_P_CAUSAL_DISCRIMINATE,
        reason=(
            "Open causal claims carry machine-checkable predictions; the "
            "journals, not the author, decide which survive."
        ),
        evidence=(f"discriminable_claims={pending}",),
        risk="reversible",
    )


def _candidate_unexplained_observation(count: int) -> BestNextAction | None:
    """Observations of own failures without explanations."""
    try:
        naked = int(count)
    except (TypeError, ValueError):
        naked = 0
    if naked < 1:
        return None
    return BestNextAction(
        action="explain_causal_observation",
        title="Explain one of the agent's own recorded failures",
        severity="medium",
        priority=_P_CAUSAL_CLIMB,
        reason=(
            "Recorded failure observations have no competing explanations; "
            "understanding own defects outranks admin debt."
        ),
        evidence=(f"unexplained_observations={naked}",),
        risk="reversible",
    )


def _candidate_dry_run_stuck(dry_run_streak: int) -> BestNextAction | None:
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
    triage: TriageReport | None,
    inbox_pending: int,
) -> BestNextAction | None:
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
    *,
    suppressed: list[BestNextAction] | None = None,
    off_subject: list[BestNextAction] | None = None,
    unknown_subject: list[BestNextAction] | None = None,
    goal_subject: str | None = None,
) -> BestNextAction:
    suppressed = suppressed or []
    off_subject = off_subject or []
    unknown_subject = unknown_subject or []
    evidence = [
        f"tests_health={tests_health}, result_status={result_status}",
        f"{int(inbox_pending or 0)} pending approval item(s)",
    ]
    unknowns = ["whether a problem exists that no current signal exposes"]
    if off_subject or unknown_subject:
        # A separate reason, not a general bag: what was set aside BY SUBJECT was never confirmed,
        # so calling it confirmed would lie about the idle reason.
        subject = goal_subject or "the goal"
        parts: list[str] = []
        if off_subject:
            parts.append(f"{len(off_subject)} about another subject")
        if unknown_subject:
            # "Subject unknown" is not "subject other": on live data that is the majority (most
            # defect records name no file).
            parts.append(f"{len(unknown_subject)} naming no subject at all")
        reason = (
            f"Nothing admissible for the chosen goal ({subject}): "
            + ", ".join(parts) + "."
        )
        if off_subject:
            evidence.append(
                "set aside as off-subject: "
                + ", ".join(sorted({c.action for c in off_subject}))
            )
        if unknown_subject:
            evidence.append(
                "set aside with unknown subject: "
                + ", ".join(sorted({c.action for c in unknown_subject}))
            )
        unknowns.append(
            "whether the goal's subject deserves work that no current signal proposes"
        )
        return BestNextAction(
            action="observe",
            title="Observe: nothing on the goal's subject warrants action",
            severity="none",
            priority=_P_OBSERVE,
            reason=reason,
            evidence=tuple(evidence),
            unknowns=tuple(unknowns),
            target_path=goal_subject,
        )
    if suppressed:
        names = ", ".join(sorted({c.action for c in suppressed}))
        reason = (
            "No unacknowledged signal warrants action: the only active alert(s) "
            "have been acknowledged by the operator."
        )
        evidence.append(f"acknowledged (suppressed) alert(s) still present: {names}")
        unknowns.append(
            "whether the acknowledged condition has since worsened — re-check before clearing the ack"
        )
    else:
        reason = (
            "No failing tests, no errors, no inbox debt, and the daemon is live: "
            "nothing warrants action now."
        )
    return BestNextAction(
        action="observe",
        title="No pressing action — stay in honest observation",
        severity="none",
        priority=_P_OBSERVE,
        reason=reason,
        evidence=tuple(evidence),
        unknowns=tuple(unknowns),
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
