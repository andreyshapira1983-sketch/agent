"""Record self-build / self-apply attempt outcomes into episodic memory.

The agent must remember what it tried and — crucially — WHY an attempt failed, so
lessons accumulate in its long-term (episodic) memory instead of every failure
requiring a human to re-investigate from scratch. Without this, a rolled-back
self-apply leaves no trace the agent can recall later ("splitting core/campaign.py
failed because the new module was not registered in the anatomy index").

Every self-build touch-point journals here — the manual ``:self-build-produce`` /
``:self-apply-run`` operator commands AND the autonomous runtime when it proposes
a split on its own tick:

* ``self-build-produce`` — what the head decided (proposed / critic_veto /
  value_veto / no_patch) and the precise reason / veto list;
* ``self-apply-run`` — whether the apply committed or rolled back, and why
  (e.g. "targeted tests failed").

Episodes are tagged ``lesson`` so the episodic store never evicts them. This is
strictly best-effort: it never raises and never blocks the caller.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.self_improvement_issues import (
    DEFAULT_ISSUE_PATH,
    SelfImprovementIssueRegistry,
)
from core.veto_cause import veto_blames_the_target
from core.writer_completion import COMPLETION_BY_OUTCOME

#: The four pre-flight gates: they ask «may I start» and run before any
#: pipeline work, so a run they refused produced NO experience. Such an
#: episode is a status line — banked once (searchable under its status tag),
#: never tagged `lesson`, never repeated while an identical row stands.
#: Measured 2026-08-22: 64 wait-rows held half the protected set, 41 of them
#: minted by one unanswered approval. Full account: docs/CODE_NOTES.md,
#: "A blocked gate is not a lesson".
_GATE_WAIT_STATUSES: frozenset[str] = frozenset({
    "budget_kill_switch", "budget_wait", "approval_wait", "dirty_tree_wait",
})

#: Пустой ход производителя: цели не нашлось, править нечего. Замер
#: 2026-09-22: 275 таких записей из 300 в опыте — они вытеснили ВСЮ настоящую
#: работу (сегодня ноль эпизодов с книгами и ноль успехов), и драйвы
#: компетентности вечно показывали «успешной задачи ни разу».
_NO_WORK_STATUSES: frozenset[str] = frozenset({
    "no_grounded_target", "no_patch", "skipped", "no_llm", "idle", "none",
})
_DEDUP_STATUSES: frozenset[str] = _GATE_WAIT_STATUSES | _NO_WORK_STATUSES

# Map each command status to a coarse episodic outcome the agent already
# understands (success / partial / failed).
_OUTCOME_BY_STATUS: dict[str, str] = {
    # ── self-build produce ───────────────────────────────────────────────
    "proposed": "success",
    "critic_veto": "failed",
    "value_veto": "failed",
    "no_patch": "partial",
    "budget_wait": "partial",
    "approval_wait": "partial",
    "dirty_tree_wait": "partial",
    "budget_kill_switch": "partial",
    # ── self-apply run ───────────────────────────────────────────────────
    "committed_local": "success",
    "rolled_back": "failed",
    "blocked": "failed",
    "error": "failed",
}

# The completion axis is settled HERE, at banking, exactly as the cycle settles
# it (`core/smart_memory.py:1341`), and never recomputed afterwards (MIR-057).
# Leaving it unset was not neutral: `admit_for_storage` grants a `lesson`-tagged
# record `usage_eligible=True` (the MIR-042 fix, so these lessons stop being
# invisible), and retrieval then replays it while it carries no verdict at all —
# the one thing `test_no_legacy_episode_in_the_live_store_is_ever_admitted`
# forbids. `outcome` already answers the question, so there is nothing to infer.
#
# The table itself lives in `core/writer_completion.py`: the backfill of rows
# banked before this fix must replay *this* rule, and a shared domain table is
# what keeps the two from drifting into separate opinions.
_COMPLETION_BY_OUTCOME = COMPLETION_BY_OUTCOME


#: Tags exist so a later attempt can FIND this episode by file, not to hold a
#: manifest of the change. A wide split would otherwise bury the other tags.
_MAX_PATH_TAGS = 10


def _touched_paths(result: dict[str, Any]) -> list[str]:
    """Repo-relative files this attempt changed, normalised for tag lookup."""
    raw = result.get("files_changed") or []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        path = str(item or "").replace("\\", "/").strip()
        if path and path not in out:
            out.append(path)
    return out[:_MAX_PATH_TAGS]


def build_self_build_episode(
    kind: str, result: dict[str, Any], *, trace_id: str = ""
) -> Any:
    """Build an :class:`EpisodeRecord` describing one attempt (no I/O).

    ``kind`` is ``"self-build-produce"`` or ``"self-apply-run"``; ``result`` is the
    command's own result dict. Returns ``None`` if smart-memory is unavailable.
    """
    try:
        from core.smart_memory import EpisodeRecord
    except Exception:  # noqa: BLE001 — memory journaling is optional
        return None

    status = str(result.get("status") or "unknown")
    outcome = _OUTCOME_BY_STATUS.get(status, "partial")
    target = str(result.get("target_path") or "")
    reason = str(result.get("reason") or "")
    proposal_id = str(result.get("proposal_id") or result.get("approval_id") or "")
    veto = result.get("veto_reasons") or []

    if kind == "self-apply-run":
        goal = f"apply self-build proposal {proposal_id or '?'}".strip()
        details: list[str] = []
        files = result.get("files_changed") or []
        if files:
            details.append(f"files={list(files)}")
        rollback = result.get("rollback_status")
        if rollback and rollback != "none":
            details.append(f"rollback={rollback}")
        summary = f"self-apply {status}: {reason}"
        if details:
            summary += " (" + "; ".join(details) + ")"
    else:  # self-build-produce (and any future producer trigger)
        goal = f"produce self-build patch for {target or 'backlog candidate'}"
        summary = f"self-build {status}: {reason}"
        if veto:
            summary += " | veto: " + "; ".join(str(v) for v in veto)

    # `lesson` is what confers eligibility, eviction protection and retrieval
    # priority (MIR-115) — a pre-flight refusal earns none of that.
    # Пустой ход — тоже не урок: 2026-09-23 замер показал 287 таких записей из
    # 300, и все с меткой «урок», то есть под защитой от вытеснения. Штатная
    # чистка дублей их не трогала (0 из 287), опыт врал «успехов ни разу», а
    # драйв компетентности вечно перебивал самопочинку. Метка урока — за
    # сделанную работу; за её отсутствие она не выдаётся.
    tags = (["self-build", kind, status, outcome]
            if status in _DEDUP_STATUSES
            else ["self-build", "lesson", kind, status, outcome])
    if status == "critic_veto":
        # Which kind of veto this was decides whether the target goes on the
        # cooldown list. Settled here, where the reasons are still in hand.
        blames_target = veto_blames_the_target(
            reason or "; ".join(str(v) for v in veto)
        )
        tags.append("veto_judgement" if blames_target else "veto_pipeline")
    if target:
        tags.append(target)
    # An apply result names `files_changed`, never `target_path`, so the episode
    # used to carry no path at all and `recent_self_build_lessons(agent, file)`
    # — which needs the path tag — found nothing. Measured on the live store:
    # two of three rollbacks were the same file, the same test and the same
    # assertion, because the first lesson was invisible to the second attempt.
    for path in _touched_paths(result):
        tags.append(path)

    # MIR-121's write side: record what the run READ. Until 2026-08-22 every
    # lesson this writer minted carried no source_labels at all, so «no
    # web-derived lesson was found» was a statement about the instrument, not
    # the store — and `_lesson_provenance_disqualified` filtered a `memory:`
    # label nothing ever wrote. The producer reports its reads in `sources`;
    # older callers fall back to the target and the files an apply changed.
    sources = [str(s) for s in (result.get("sources") or []) if str(s).strip()]
    if not sources:
        if target:
            sources.append(f"file:{target}")
        sources.extend(f"file:{p}" for p in _touched_paths(result))

    return EpisodeRecord(
        goal=goal[:500],
        question=kind,
        outcome=outcome,  # type: ignore[arg-type]  # one of success/partial/failed
        summary=summary[:2000],
        tags=tuple(dict.fromkeys(t for t in tags if t)),  # dedup, keep order
        source_labels=tuple(dict.fromkeys(sources)),
        # Семейная связка тика (MIR-184): отказ продукта обязан быть сшиваем
        # с эпизодом-успехом того же прогона, иначе он структурно неслышим.
        trace_id=str(trace_id or ""),
        completion_state=_COMPLETION_BY_OUTCOME.get(  # type: ignore[arg-type]
            outcome, "unknown"
        ),
    )


def record_self_build_episode(
    agent: Any, *, kind: str, result: dict[str, Any], trace_id: str = ""
) -> bool:
    """Persist one attempt outcome to the agent's episodic memory.

    Returns True when an episode was written, False otherwise. Best-effort: any
    failure (no store, bad record, disk error) is swallowed so the caller
    command / tick is never broken by memory journaling.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None:
            return False
        episode = build_self_build_episode(kind, result, trace_id=trace_id)
        if episode is None:
            return False
        # MIR-090's writer half: a producer that hits the same gate twice must
        # not bank a second identical episode — the unattended tick retried one
        # blocked gate 32 times on 2026-08-16 and banked all 32. Keyed on
        # CONTENT, not the status label (label-keyed dedup destroys real
        # records: ten distinct answers under one question, measured in
        # docs/audit/archive/MEMORY_CONSOLIDATION_MEASUREMENT.md). Gate waits only —
        # an identical genuine veto tomorrow may mean "still failing", and
        # judging that is the hygiene collapser's job, not the writer's.
        if str(result.get("status") or "") in _DEDUP_STATUSES:
            key = (episode.goal, episode.question, episode.summary, episode.outcome)
            for old in store.load():
                if (old.goal, old.question, old.summary, old.outcome) == key:
                    return False
        store.save(episode)
    except Exception:  # noqa: BLE001 — journaling must never break the caller
        return False
    return True


def _with_untagged_lessons(
    store: Any, target: str, found: list[Any], *, limit: int
) -> list[Any]:
    """Add failed episodes whose summary names the file but whose tags do not."""
    seen = {id(e) for e in found}
    out = list(found)
    for episode in store.search_by_tags(["self-build", "failed"], limit=limit * 8):
        if len(out) >= limit:
            break
        if id(episode) in seen:
            continue
        if target in str(getattr(episode, "summary", "") or ""):
            out.append(episode)
    return out


def recent_self_build_lessons(agent: Any, target: str, *, limit: int = 3) -> list[str]:
    """Return short summaries of PAST FAILED self-build attempts for
    ``target``.

    Reads the agent's episodic memory for lesson episodes tagged with the
    target path AND a failed outcome, newest first, so the Builder can be
    warned not to repeat a mistake it already made on this exact file (e.g.
    "left a dangling import to a class it forgot to move"). Best-effort:
    returns ``[]`` when memory is unavailable or empty, and never raises.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None or not target:
            return []
        episodes = store.search_by_tags(
            ["self-build", "failed", target], limit=limit
        )
        if len(episodes) < limit:
            episodes = _with_untagged_lessons(store, target, episodes, limit=limit)
        lessons: list[str] = []
        for episode in episodes:
            summary = str(getattr(episode, "summary", "") or "").strip()
            if summary:
                lessons.append(summary[:300])
    except Exception:  # noqa: BLE001 — lesson recall must never break the caller
        return []
    else:
        return lessons


def _veto_was_about_the_target(episode: Any, tags: tuple[str, ...]) -> bool:
    """Read the classification tag; fall back to the summary for older rows.

    Episodes banked before the classification existed carry neither tag, and
    they are the ones already sitting in the store — judging them by their own
    veto text is the same rule, applied to the only evidence they kept.
    """
    if "veto_pipeline" in tags:
        return False
    if "veto_judgement" in tags:
        return True
    return veto_blames_the_target(str(getattr(episode, "summary", "") or ""))


def recently_vetoed_self_build_targets(agent: Any, *, limit: int = 20) -> frozenset[str]:
    """Return concrete target paths whose LAST self-build attempt was critic-vetoed.

    Reads the agent's episodic memory for lesson episodes tagged with BOTH
    ``self-build`` and ``critic_veto`` and extracts the file-path tag each such
    episode carries (see :func:`build_self_build_episode`). The producer uses this
    as a short cooldown set so the very next ``:self-build-produce`` run skips a
    target it just failed on and advances to the next grounded candidate instead
    of re-picking the same wall. Best-effort: returns an empty set when memory is
    unavailable or empty, and never raises.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None:
            return frozenset()
        episodes = store.search_by_tags(["self-build", "critic_veto"], limit=limit)
        targets: set[str] = set()
        for episode in episodes:
            tags = tuple(getattr(episode, "tags", ()) or ())
            if not _veto_was_about_the_target(episode, tags):
                continue
            for tag in tags:
                candidate = str(tag).replace("\\", "/").strip()
                if "/" in candidate and (
                    candidate.endswith((".py", ".md"))
                ):
                    targets.add(candidate)
        return frozenset(targets)
    except Exception:  # noqa: BLE001 — cooldown recall must never break the caller
        return frozenset()


def _recent_self_improvement_events(
    agent: Any,
    workspace: Path,
    *,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, max_age_days))
    records: list[dict[str, Any]] = []

    def add(created_at: object, text: object, kind: str = "failure", **extra: Any) -> None:
        try:
            stamp = datetime.fromisoformat(str(created_at))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return
        message = " ".join(str(text or "").split())
        if stamp >= cutoff and message:
            records.append({"stamp": stamp, "text": message[:500], "kind": kind, **extra})

    try:
        store = getattr(agent, "episodic_store", None)
        for episode in (store.load()[-40:] if store is not None else []):
            text = f"{getattr(episode, 'question', '')}: {getattr(episode, 'summary', '')}"
            lowered = text.casefold()
            self_improvement = any(
                term in lowered
                for term in ("self-apply", "self-build", "self-split", "splitter", "mixin", "repair")
            )
            # `outcome` is the structural fact; the wording is the author's
            # choice. The table this replaced listed rolled_back / rollback /
            # failed / rejected / duplicate base class / too many lines, so a
            # self-build run that timed out, was refused by policy, or came
            # back empty was lost — three such shapes measured 2026-08-15.
            # `partial` counts: a repair that half-happened is exactly what a
            # durable issue is for. Measured on the live store, this moves the
            # admitted set 23 -> 42 of 200, and the registry collapses repeats
            # by fingerprint, so it is a wider net rather than a flood.
            failed = getattr(episode, "outcome", "") != "success"
            # The run's own detectors are a first-class route in, independent
            # of the word table above. A word table decides intent from
            # vocabulary, and a fabricated citation says none of these words:
            # measured 2026-08-14, the agent invented four sources, its verifier
            # caught it (`citation_fabricated`) and nothing durable recorded it,
            # so the defect could never be counted and repetition never noticed.
            signals = [str(s) for s in (getattr(episode, "defect_signals", None) or [])]
            detector_text = _detector_failure_text(signals, text)
            if detector_text is not None:
                add(getattr(episode, "created_at", ""), detector_text)
            elif self_improvement and failed:
                add(getattr(episode, "created_at", ""), text)
    except Exception:  # noqa: BLE001, S110 — advisory history must never break CLI
        pass

    try:
        paths = sorted((workspace / "logs").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-12:]
        for path in paths:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                event = str(row.get("event") or "")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                status = str(payload.get("status") or "")
                fingerprint = str(payload.get("issue_fingerprint") or payload.get("fingerprint") or "")
                action = str(payload.get("issue_action") or payload.get("action") or "")
                if event == "self_apply_run" and status in {"rolled_back", "blocked", "error"}:
                    add(row.get("ts"), f"self-apply {status}: {payload}")
                elif event == "self_apply_run" and status == "committed_local" and (fingerprint or action):
                    add(row.get("ts"), "matching self-apply committed successfully", "resolved",
                        fingerprint=fingerprint, action=action)
                elif event == "repair_proposal_result" and status == "rejected":
                    warnings = "; ".join(str(x) for x in payload.get("warnings") or ())
                    add(row.get("ts"), f"repair proposal rejected: {warnings}", attach=True,
                        fingerprint=fingerprint, action=action)
                elif event == "self_split_plan" and status not in {"planned", ""}:
                    add(row.get("ts"), f"self-split {status}: {payload.get('reason', '')}")
                elif event in {"self_improvement_issue_verified", "self_improvement_issue_resolved"}:
                    kind = "verified" if event.endswith("verified") else "resolved"
                    add(row.get("ts"), payload.get("evidence") or event, kind,
                        fingerprint=fingerprint, action=action)
    except Exception:  # noqa: BLE001, S110 — malformed traces are ignored best-effort
        pass

    records.sort(key=lambda record: record["stamp"])
    return records


def has_fresh_self_improvement_failure(
    agent: Any,
    workspace: Path,
    *,
    max_age_hours: int = 24,
) -> bool:
    """Была ли СВЕЖАЯ неудача самоулучшения — скоропортящийся сигнал.

    Свежая собственная неудача обгоняет рутинный бэклог в выборе действия
    (MIR-186): контекст поломки выветривается — живой зонд 2026-08-28
    расходился с тиковым состоянием уже через 3,5 часа, — а измеренный
    раскол подождёт до завтра. Сутки — окно, в котором трасса, дерево и
    память ещё говорят об одном и том же.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, max_age_hours))
    return any(
        r["kind"] == "failure" and r["stamp"] >= cutoff
        for r in _recent_self_improvement_events(agent, workspace, max_age_days=2)
    )


def recent_unresolved_self_improvement_failures(
    agent: Any,
    workspace: Path,
    *,
    max_age_days: int = 7,
    limit: int = 4,
) -> tuple[str, ...]:
    """Return recent failure evidence; unrelated successes never erase it."""
    failures = [
        (record["stamp"], record["text"])
        for record in _recent_self_improvement_events(
            agent, workspace, max_age_days=max_age_days
        )
        if record["kind"] == "failure"
    ]
    failures.sort(reverse=True)
    return tuple(dict.fromkeys(text for _stamp, text in failures))[: max(1, limit)]


#: Sensor families whose firings are MEASURED unreliable and must not mint
#: durable open defects. `reasoning_action_mismatch`: 71% firing rate,
#: accusations dominated by its own table defects (MIR-015), and its
#: escalation-contract default forbids attaching enforcement to the family —
#: a permanent open issue per firing is enforcement wearing a ledger's
#: clothes. `user_contract_unrepresented` rides the same prose-vs-plan table.
#: The verifier-caught signals (`content_refuted`, `citation_fabricated`)
#: stay first-class: a fabricated citation nothing durable recorded was this
#: route's founding case (2026-08-14).
_UNRELIABLE_DETECTOR_SIGNALS: frozenset[str] = frozenset({
    "reasoning_action_mismatch",
    "user_contract_unrepresented",
})


def _detector_failure_text(signals: list[str], text: str) -> str | None:
    """Failure text for the durable registry, or None when nothing reliable
    fired. Unreliable signals are dropped rather than carried — otherwise they
    ride into the record on a reliable signal's coat-tails."""
    reliable = [s for s in signals if s and s not in _UNRELIABLE_DETECTOR_SIGNALS]
    if not reliable:
        return None
    return f"detectors {', '.join(reliable)}: {text}"


def sync_self_improvement_issue_registry(
    agent: Any,
    workspace: Path,
    *,
    max_age_days: int = 7,
) -> SelfImprovementIssueRegistry:
    """Persist recent failures and apply only explicitly matching transitions."""
    registry = SelfImprovementIssueRegistry(workspace / DEFAULT_ISSUE_PATH)
    # Свой сбой становится дефектом, а не только наблюдением (2026-09-22:
    # за день ни одной записи от агента при десятках срабатываний детекторов).
    from core.defect_intake import intake_observations

    intake_observations(workspace, registry)
    for record in _recent_self_improvement_events(
        agent, workspace, max_age_days=max_age_days
    ):
        observed_at = record["stamp"].isoformat()
        if record["kind"] == "failure":
            fingerprint = str(record.get("fingerprint") or "")
            action = str(record.get("action") or "")
            if record.get("attach") and (fingerprint or action):
                matched = registry.transition(
                    status="open", observed_at=observed_at,
                    fingerprint=fingerprint, action=action, evidence=record["text"],
                )
                if matched is not None:
                    continue
            if record.get("attach") and registry.unresolved():
                current = registry.unresolved()[0]
                registry.transition(
                    status=current.status, observed_at=observed_at,
                    fingerprint=current.fingerprint, evidence=record["text"],
                )
            else:
                registry.upsert_failure(record["text"], observed_at)
        else:
            registry.transition(
                status=record["kind"], observed_at=observed_at,
                fingerprint=str(record.get("fingerprint") or ""),
                action=str(record.get("action") or ""), evidence=record["text"],
            )
    return registry


def idle_self_direction(workspace: Path, heartbeat: dict | None = None) -> dict:
    """What the agent would do next when nobody queued anything for it.

    Proposes only; never acts. Costs no model call. `heartbeat=None` means
    read it here. Why it exists: docs/CODE_NOTES.md, "Idle self-direction".
    """
    from core.best_next_action import format_best_next_action, select_best_next_action
    from core.heartbeat_io import heartbeat_age_seconds, is_stale, read_heartbeat
    from core.smart_memory import EpisodicMemoryStore

    if heartbeat is None:
        heartbeat = read_heartbeat(workspace)

    class _EpisodesOnly:
        """The one attribute the sync reads — see `_recent_self_improvement_events`."""

        def __init__(self, store: Any) -> None:
            self.episodic_store = store

    store = EpisodicMemoryStore(workspace / "data" / "episodic_memory.jsonl")
    registry = sync_self_improvement_issue_registry(_EpisodesOnly(store), workspace)
    open_issues = tuple(issue.to_dict() for issue in registry.unresolved())

    hb = heartbeat or {}
    action = select_best_next_action(
        result_status=str(hb.get("result_status") or "none"),
        tests_health=str(hb.get("tests_health") or "none"),
        dry_run_streak=int(hb.get("dry_run_streak") or 0),
        heartbeat_missing=heartbeat is None,
        heartbeat_stale=is_stale(heartbeat_age_seconds(heartbeat)),
        last_event=str(hb.get("event") or ""),
        inbox_pending=int(hb.get("inbox_pending_after") or 0),
        self_improvement_registry_available=True,
        open_self_improvement_issues=open_issues,
    )
    return {
        "open_issues": len(open_issues),
        "action": getattr(action, "action", ""),
        "severity": getattr(action, "severity", ""),
        "summary": format_best_next_action(action)[:400],
    }
