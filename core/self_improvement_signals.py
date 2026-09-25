"""Сигналы неудач самоулучшения: откуда они берутся и куда уходят.

Вынесено из core/self_build_memory.py: тот модуль ЗАПИСЫВАЕТ исходы попыток
самосборки в эпизодическую память и вспоминает уроки по адресу, а здесь —
ЧТЕНИЕ неудач из эпизодов и журналов logs/*.jsonl
(`_recent_self_improvement_events`), вопросы к ним («есть ли свежая
неудача», «какие не разрешены»), перенос в реестр проблем
(`sync_self_improvement_issue_registry`, core/self_improvement_issues.py) и
выбор действия, когда агенту никто ничего не поставил
(`idle_self_direction`).
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
