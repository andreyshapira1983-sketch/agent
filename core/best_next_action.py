"""Priority intelligence: choose the single most important next action.

A bounded-autonomy agent is only useful if it is *initiative inside a safe
corridor*: it must notice problems, connect signals, and surface the **one**
action that matters most right now — with evidence, a risk estimate, and an
honest account of what it does **not** know. It must NOT spray ten
speculative proposals every tick.

* it reads structured signals (passed in) and returns one recommendation; *
it performs no I/O, mutates nothing, and never executes the action; * it
always returns exactly one :class:`BestNextAction` — even "just observe" —
so the agent is forced to commit to a single priority and justify it.

The selection is deterministic: every candidate carries a fixed priority
score and the highest score wins. Ties resolve by score then by a stable
order, so the same signals always yield the same advice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from core.approval_triage import TriageReport
from core.self_improvement_issues import (
    SelfImprovementIssue,
    suppress_generic_issue_duplicates,
)

Severity = Literal["critical", "high", "medium", "low", "none"]


# Priority scores. Higher wins. Chosen so the ordering is obvious and stable:
# if the daemon is not even ticking, nothing else can be trusted; a hard tick
# error outranks a test failure; a test failure outranks softer hygiene work.
_P_DAEMON_DOWN = 100      # heartbeat missing/stale: agent may not be running
_P_TICK_ERROR = 90        # last tick raised: the loop itself is broken
_P_TESTS_FAIL = 80        # concrete failing tests: minimal repair is provable
_P_TESTS_INCONCLUSIVE = 60  # timed-out/unknown: must not be read as healthy
_P_ENGINEERING_TASK = 59  # the campaign goal asks for engineering work: the
                          # road charter -> backlog (2026-08-19); outranks the
                          # document so a goal naming both builds, not writes.
_P_CHARTER_DOCUMENT = 58  # the campaign goal itself asks for a doctrine draft:
#   above the durable-issue habit (55) — live 2026-08-15 the head chose "draft
#   the contract" and the hands did habitual repair — below health alarms (60+)
_P_EXTERNAL_STUDY = 57    # the goal asks to STUDY the outside world: above the
#   repair habit (55), below the doc goal (58) — a request to write is more
#   concrete than a request to read
_P_SELF_IMPROVEMENT_FAILURE = 55  # recent rollback/rejection despite clean health
_P_INBOX_DEBT = 50        # duplicate proposals accumulating into admin debt
_P_DRY_RUN_STUCK = 40     # many dry-run ticks: never applied anything, ask why
_P_INBOX_BACKLOG = 30     # large pending queue with no clear duplicates
_P_OBSERVE = 0            # nothing pressing: stay in honest observation


# Thresholds (deliberately conservative — advice, not automation).
_DRY_RUN_STREAK_ALERT = 5     # ~5 consecutive dry-run ticks before nudging
_INBOX_DEBT_DUPLICATES = 3    # this many duplicates is real debt, not noise
_INBOX_BACKLOG_PENDING = 12   # backlog worth a dedicated review pass

# Severities an operator may acknowledge away (see core.alert_ack). Objective
# breakages (critical/high) are intentionally excluded — never suppressible.
_SUPPRESSIBLE_SEVERITIES = frozenset({"medium", "low"})

# The advisory alert actions (exactly the medium/low candidates below) that an
# operator is allowed to acknowledge. Kept as an explicit registry so the REPL
# can validate an ack request from the action NAME alone, before any signals
# are gathered. Critical/high actions are deliberately absent.
_SUPPRESSIBLE_ACTIONS = frozenset({
    "reduce_inbox_duplicate_debt",  # medium
    "review_dry_run_stall",         # medium
    "review_inbox_backlog",         # low
})


def is_suppressible_alert(action: str) -> bool:
    """Whether an alert with this action name may be acknowledged. Pure."""
    return str(action) in _SUPPRESSIBLE_ACTIONS


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
    #: Объект, НАЗВАННЫЙ в основании решения, когда основание его называет.
    #: H-20 (binding drift) в docs/audit/HISTORICAL_FAILURE_LEDGER.md:
    #: обязательство может пережить перерыв и при этом потерять связь с
    #: конкретным предметом. Здесь так и было — `_PY_TARGET_RE` находил имя
    #: файла, по нему цель признавалась инженерной, и совпадение
    #: ВЫБРАСЫВАЛОСЬ: имя оставалось только внутри `evidence`, дословным
    #: эхом текста цели, откуда потребителю его не взять. `None` значит
    #: «основание не называло объекта», а не «объект неизвестен».
    target_path: str | None = None
    confidence: float = 0.0
    #: HOW this action was selected — a fact derived at the selection site, not
    #: a judgement. One of `no_candidate` (nothing was admissible),
    #: `sole_candidate` (one was active, so no selection occurred) or
    #: `priority_table` (two or more competed and the developer's `_P_*`
    #: literals picked the winner). Deliberately no `agent_deliberation` value:
    #: the census measured zero agent-owned decision boundaries, and a label
    #: without a mechanism behind it is ceremony (MIR-117, MIR-119).
    decided_by: str = "unrecorded"
    #: WHERE the grounds came from — derived from which input the candidate
    #: read, so it is a fact rather than an assignment. `operator_goal` (the
    #: operator's own text), `observed_state` (live signals) or
    #: `retained_record` (durable state carried from earlier runs). The third
    #: is what makes "did experience change this decision" answerable at all,
    #: and what lets a post-mortem separate the agent's own grounds from ones
    #: a human supplied (MIR-117, the human-deferred-authorship path).
    grounds: str = "unrecorded"
    #: How many candidates were still in the race when the winner was taken —
    #: the count describes the race that happened, so a suppressed candidate is
    #: not counted as a competitor.
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


#: Глагол черновика + имя .md в цели — иначе цель не документная.
_DRAFT_VERB_RE = re.compile(
    r"\b(draft|write|compose|напиш|черновик|состав)", re.IGNORECASE
)
_DOC_NAME_RE = re.compile(r"[\w/.\-]+\.md\b")


def doc_target_from_goal(goal: str) -> str:
    """Repo-путь документа, который цель просит написать, или "".

    Явный путь в цели сохраняется; голое имя едет в knowledge/doctrine/future/
    — дом целевых (ещё не действующих) документов доктрины.
    """
    text = str(goal or "")
    if not _DRAFT_VERB_RE.search(text):
        return ""
    match = _DOC_NAME_RE.search(text)
    if not match:
        return ""
    name = match.group(0).strip("'\"")
    if "/" in name:
        return name
    return f"knowledge/doctrine/future/{name}"


#: Учебная цель: глагол изучения + внешний мир. Решение оператора 2026-08-16
#: («строй автомат»): чтение внешнего мира — законная работа кампании.
_STUDY_VERB_RE = re.compile(r"\b(изучи|прочитай|почитай|посмотри|study|read|research)",
                            re.IGNORECASE)
_OUTSIDE_RE = re.compile(r"интернет|сайт|http|www\.|в вебе|\bweb\b", re.IGNORECASE)


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


#: Инженерная цель хартии: слова о бэклоге/расколе/падающем тесте/разрыве.
_ENGINEERING_GOAL_RE = re.compile(
    r"(?i)backlog|бэклог|self-build|failing.?test|падающ\w+ тест|proven gap"
    r"|доказанн\w+ разрыв|раскол|module.?split|split (?:of|proposal|plan)"
    r"|engineering candidate|инженерн\w+ кандидат|разбиени\w+ модул",
)
#: Структурная половина (три словарных промаха за 2026-08-19): цель, которая
#: НАЗЫВАЕТ .py-файл и говорит об инженерном действии над ним, — инженерная,
#: какими бы словами её ни сформулировала модель.
_PY_TARGET_RE = re.compile(r"\b[\w/\\.-]+\.py\b")
_ENGINEERING_CONTEXT_RE = re.compile(
    r"(?i)split|refactor|restructur|decompos|модул|module|раздели|разбей|почини|fix",
)


def _is_engineering_goal(text: str) -> bool:
    if _ENGINEERING_GOAL_RE.search(text):
        return True
    return bool(_PY_TARGET_RE.search(text) and _ENGINEERING_CONTEXT_RE.search(text))


#: Токен, похожий на путь: минимум один разделитель каталогов. Голое слово
#: путём не считается — иначе предметом цели становилась бы любая фраза.
_SUBJECT_TOKEN_RE = re.compile(r"[\w.-]+(?:[/\\][\w.-]+)+")


def resolve_goal_subject(text: str, *, exists) -> str | None:
    """Артефакт, О КОТОРОМ цель, — по существованию, а не по суффиксу.

    Замер, отвергнутые варианты и границы: MIR-158 в docs/audit/MASTER_ISSUE_REGISTRY.md.
    `exists` внедряется, чтобы решающая таблица осталась чистой функцией.
    """
    for raw in _SUBJECT_TOKEN_RE.findall(str(text or "")):
        token = raw.replace("\\", "/").strip("`'\",.;:()[]")
        for candidate in (token, f"{token}.py"):
            if candidate and exists(candidate):
                return candidate
    return None


def unresolved_goal_targets(text: str, *, exists) -> tuple[str, ...]:
    """Пути, НАЗВАННЫЕ целью и не существующие в рабочей области.

    Замер, отвергнутые варианты и границы: MIR-161 в docs/audit/MASTER_ISSUE_REGISTRY.md.
    Нужна, чтобы отказ разрешения не был неотличим от отсутствия имени.
    """
    out: list[str] = []
    for raw in _SUBJECT_TOKEN_RE.findall(str(text or "")):
        token = raw.replace("\\", "/").strip("`'\",.;:()[]")
        if not token or exists(token) or exists(f"{token}.py"):
            continue
        out.append(token)
    return tuple(dict.fromkeys(out))


def _named_target(text: str) -> str | None:
    """Файл, названный в тексте цели, если он там назван.

    Отдельная функция, а не второй разбор внутри решения: тот же `_PY_TARGET_RE`,
    которым цель признаётся инженерной, отдаёт СОВПАДЕНИЕ, а не только «да».
    Решение, принятое ПОТОМУ ЧТО файл назван, обязано этот файл унести.
    """
    match = _PY_TARGET_RE.search(text or "")
    return match.group(0) if match else None


def _candidate_engineering_task(goal: str) -> BestNextAction | None:
    """Дорога от хартии к бэклогу (2026-08-19): цель, просящая инженерную
    работу, рождает self-build/Stage A ЗАЯВКУ — язык и провод, не права."""
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


def _candidate_goal_names_missing(
    goal: str,
    goal_subject: str | None,
    goal_names_missing: tuple[str, ...] | None,
    active: list[BestNextAction],
) -> BestNextAction | None:
    """Названный отказ, когда цель зовёт то, чего в рабочей области нет.

    Замер, отвергнутые варианты и границы: MIR-161 в docs/audit/MASTER_ISSUE_REGISTRY.md.
    Молчание здесь означало бы работу по чужому делу под именем этой цели.
    """
    if goal_subject or not goal_names_missing:
        return None
    if _DRAFT_VERB_RE.search(str(goal or "")):
        return None  # цель просит СОЗДАТЬ файл — это законно
    if [c for c in active if c.severity in ("critical", "high")]:
        return None  # поломка сильнее цели всегда
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


def _partition_by_subject(
    active: list[BestNextAction], goal_subject: str,
) -> tuple[list[BestNextAction], list[BestNextAction], list[BestNextAction]]:
    """Разложить кандидатов на «по предмету», «про другой», «предмет неизвестен».

    Замер, отвергнутые варианты и границы: MIR-162 в docs/audit/MASTER_ISSUE_REGISTRY.md.
    Третий класс существует потому, что «не знаю, про что» — не то же самое,
    что «знаю, что про другое»; сваливать их в одну кучу значит выдавать
    незнание за знание.
    """
    on: list[BestNextAction] = []
    off: list[BestNextAction] = []
    unknown: list[BestNextAction] = []
    for candidate in active:
        if (
            candidate.severity in ("critical", "high")
            or candidate.target_path == goal_subject
            or candidate.grounds == "operator_goal"
        ):
            on.append(candidate)
        elif candidate.target_path is None:
            unknown.append(candidate)
        else:
            off.append(candidate)
    return on, off, unknown


def select_best_next_action(  # noqa: PLR0913 — flat: depth 1, all 2 returns are guard clauses
    *,
    goal: str = "",
    #: Артефакт, О КОТОРОМ цель, — разрешает ВЫЗЫВАЮЩИЙ (`resolve_goal_subject`),
    #: потому что разрешение требует файловой системы, а таблица решений чистая.
    #: `None` значит «цель предмета не назвала», и тогда она ничего не сужает.
    goal_subject: str | None = None,
    #: Пути, названные целью и НЕ существующие. Разрешает вызывающий
    #: (`unresolved_goal_targets`) — отказ разрешения не должен быть
    #: неотличим от отсутствия имени (MIR-161).
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
) -> BestNextAction:
    """Pick the single most important next action from the current signals.

    ``acknowledged`` is the set of action names the operator has accepted
    (see :mod:`core.alert_ack`). An acknowledged candidate is removed from
    the top-pick race ONLY when its severity is suppressible
    (``medium``/``low``) — objective breakages (``critical``/``high``) are
    never silenced, so you can never acknowledge away a real failure.
    Suppressed alerts are not deleted: when nothing else is pressing they
    are surfaced by the ``observe`` fallback.
    """
    candidates: list[BestNextAction] = []

    def admit(candidate: BestNextAction | None, grounds: str) -> None:
        """Tag the candidate with WHERE its grounds came from, at the one place
        where the input it read is visible. Derived, never assigned: a
        generator taking `goal` rests on the operator's text, one taking live
        signals on observation, one reading the durable registry on records
        carried from earlier runs."""
        if candidate is not None:
            candidates.append(replace(candidate, grounds=grounds))

    admit(_candidate_engineering_task(goal), "operator_goal")
    admit(_candidate_charter_document(goal), "operator_goal")
    admit(_candidate_external_study(goal), "operator_goal")

    admit(
        _candidate_daemon(heartbeat_missing, heartbeat_stale,
                          heartbeat_age_seconds, last_event),
        "observed_state",
    )
    admit(_candidate_tick_error(tick_error), "observed_state")
    admit(_candidate_tests(tests_health, result_status, failed_tests),
          "observed_state")

    # Both branches read state carried from EARLIER runs — the durable issue
    # registry, or recent failures reconstructed from episodes. That is a
    # different provenance from a live signal, and it is the one that makes the
    # memory-influence question answerable later.
    improvement = _candidate_open_self_improvement_issue(
        open_self_improvement_issues, subject=goal_subject,
    )
    if improvement is None and not self_improvement_registry_available:
        improvement = _candidate_self_improvement_failure(
            recent_self_improvement_failures
        )
    admit(improvement, "retained_record")

    admit(_candidate_inbox_debt(triage), "observed_state")
    admit(_candidate_dry_run_stuck(dry_run_streak), "observed_state")
    admit(_candidate_inbox_backlog(triage, inbox_pending), "observed_state")

    # Partition: an acknowledged advisory alert (medium/low) leaves the race so
    # the next genuine action surfaces. A critical/high candidate is NEVER
    # suppressible even if its name was acknowledged.
    active = [c for c in candidates if not _is_suppressed(c, acknowledged)]
    suppressed = [c for c in candidates if _is_suppressed(c, acknowledged)]
    off_subject: list[BestNextAction] = []
    unknown_subject: list[BestNextAction] = []

    # Цель СУЖАЕТ допустимое, а не просто добавляет кандидата. Без этого её
    # влияние было нулевым: живой прогон 2026-08-25 выбрал цель про
    # `core/subagent_registry`, её генераторы не дали ни одного кандидата, и
    # `max(priority)` отдал цикл единственному оставшемуся делу про ЧУЖОЙ файл.
    # Объективная поломка (critical/high) под сужение не попадает никогда:
    # иначе цель стала бы способом отвести взгляд от сломанных тестов.
    missing = _candidate_goal_names_missing(
        goal, goal_subject, goal_names_missing, active,
    )
    if missing is not None:
        return missing

    if goal_subject:
        active, off_subject, unknown_subject = _partition_by_subject(
            active, goal_subject,
        )

    if not active:
        fallback = _candidate_observe(
            tests_health, result_status, inbox_pending, suppressed=suppressed,
            off_subject=off_subject, unknown_subject=unknown_subject,
            goal_subject=goal_subject,
        )
        # Основание простоя — ФАКТ о том, кто его вызвал. Если гонку опустошила
        # ЦЕЛЬ (отвела всех по предмету), основанием стоит она, иначе — живые
        # сигналы. Иначе цикл, где именно цель всё и отвела, считался бы как
        # «цель не вела работу», и новый счётчик (MIR-163) врал бы о себе.
        emptied_by_goal = bool(off_subject or unknown_subject)
        return replace(
            fallback,
            decided_by="no_candidate",
            candidates_considered=0,
            grounds="operator_goal" if emptied_by_goal else "observed_state",
        )

    # Deterministic: highest priority wins; ties keep first-appended (which is
    # already the intended severity order above).
    winner = max(active, key=lambda c: c.priority)
    # Provenance recorded HERE because this is where the selection happens, and
    # it is derived rather than asserted: one active candidate means no choice
    # was made at all; two or more means the developer's `_P_*` numbers settled
    # it. Writing that down is what lets a later post-mortem tell the agent's
    # mistake from a developer's (MIR-117, the first of three empty axes).
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
        command = "review recent self-build history and propose one small read-only fix"
    return BestNextAction(
        action=action,
        title=title,
        severity="medium",
        priority=_P_SELF_IMPROVEMENT_FAILURE,
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
) -> BestNextAction | None:
    dominant = list(suppress_generic_issue_duplicates(
        SelfImprovementIssue.from_dict(issue) for issue in issues
    ))
    if subject:
        # Предмет цели ВЫБИРАЕТ запись, а не отсеивает выбранную. Живой замер
        # 2026-08-26: из 29 открытых дефектов решению предлагался ровно один —
        # первый по порядку ЗАПИСИ В ФАЙЛ, — поэтому цель про
        # `core/loop_synthesis.py` уходила в простой, хотя открытый дефект
        # ровно про этот файл лежал в том же бэклоге (MIR-160). Сортировка
        # устойчива: не совпавшие сохраняют прежний порядок.
        dominant.sort(
            key=lambda m: 0 if subject in (m.to_dict().get("related_files") or ()) else 1
        )
    for model in dominant:
        issue = model.to_dict()
        status = str(issue.get("status") or "open")
        if status == "resolved":
            continue
        fingerprint = str(issue.get("fingerprint") or "unknown")
        raw_evidence = issue.get("evidence") or ()
        # MIR-035 audit: the class merge caps evidence at 8, so the samples
        # alone cannot say whether this fired 8 times or 80. Recurrence is the
        # reason a class earns an investigation, so the count travels with it.
        # НИЖНЯЯ ГРАНИЦА, а не точный счёт. H-11 (Knight Capital, 2012):
        # старое состояние, прочитанное новым кодом, получает умолчание, и
        # умолчание УТВЕРЖДАЕТ про прошлое то, чего в нём не было. Замер живого
        # хранилища: 25 записей, заведённых до появления счётчика, приходят с
        # `occurrences=1` — притом что MIR-035 измерил 13 копий одного
        # сигнального класса. Счёт ведётся с момента, когда поле появилось, и
        # для таких рядов он занижен по построению. Знак `>=` говорит ровно это.
        seen = max(1, int(issue.get("occurrences") or 1))
        evidence = [f"durable issue {fingerprint} status={status} seen>={seen}x"]
        evidence.extend(str(item)[:300] for item in raw_evidence if str(item).strip())
        files = [str(item) for item in issue.get("related_files") or () if str(item)]
        if files:
            evidence.append("related files: " + ", ".join(files))
        return BestNextAction(
            action=str(issue.get("action") or "improve_failure_to_idea_pipeline"),
            title=str(issue.get("title") or "Resolve the open self-improvement issue"),
            severity="medium",
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
            # Предмет, названный самой записью, УНОСИТСЯ, а не оседает в тексте
            # улики: без него решение нельзя сопоставить с предметом цели, и
            # ровно так дефект побеждал цель, которая была про другой файл
            # (H-20, MIR-158).
            # Предметом решения становится ФАЙЛ, ПО КОТОРОМУ совпало, а не
            # первый из списка: запись дефекта часто называет несколько, и
            # привязка к первому отвергала цель про второй (замер 2026-08-26 на
            # живом бэклоге: `core/evidence_budget.py` уходил в простой).
            target_path=(
                subject if subject and subject in files
                else (files[0] if files else None)
            ),
            confidence=0.75,
        )
    return None


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
        # Отдельная причина, а не общий мешок: отведённое ПО ПРЕДМЕТУ никто не
        # подтверждал, и назвать его подтверждённым значило бы соврать
        # оператору о причине простоя (MIR-158).
        subject = goal_subject or "the goal"
        parts: list[str] = []
        if off_subject:
            parts.append(f"{len(off_subject)} about another subject")
        if unknown_subject:
            # «Предмет неизвестен» — не «предмет другой». На живых данных это
            # большинство: 21 запись дефектов из 29 не называет файла вовсе.
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
