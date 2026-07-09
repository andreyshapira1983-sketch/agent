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


def build_self_build_episode(kind: str, result: dict[str, Any]) -> Any:
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

    tags = ["self-build", "lesson", kind, status, outcome]
    if target:
        tags.append(target)

    return EpisodeRecord(
        goal=goal[:500],
        question=kind,
        outcome=outcome,  # type: ignore[arg-type]  # one of success/partial/failed
        summary=summary[:2000],
        tags=tuple(dict.fromkeys(t for t in tags if t)),  # dedup, keep order
    )


def record_self_build_episode(agent: Any, *, kind: str, result: dict[str, Any]) -> bool:
    """Persist one attempt outcome to the agent's episodic memory.

    Returns True when an episode was written, False otherwise. Best-effort: any
    failure (no store, bad record, disk error) is swallowed so the caller
    command / tick is never broken by memory journaling.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None:
            return False
        episode = build_self_build_episode(kind, result)
        if episode is None:
            return False
        store.save(episode)
        return True
    except Exception:  # noqa: BLE001 — journaling must never break the caller
        return False


def recent_self_build_lessons(agent: Any, target: str, *, limit: int = 3) -> list[str]:
    """Return short summaries of PAST FAILED self-build attempts for ``target``.

    Reads the agent's episodic memory for lesson episodes tagged with the target
    path AND a failed outcome, newest first, so the Builder can be warned not to
    repeat a mistake it already made on this exact file (e.g. "left a dangling
    import to a class it forgot to move"). Best-effort: returns ``[]`` when memory
    is unavailable or empty, and never raises.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None or not target:
            return []
        episodes = store.search_by_tags(
            ["self-build", "failed", target], limit=limit
        )
        lessons: list[str] = []
        for episode in episodes:
            summary = str(getattr(episode, "summary", "") or "").strip()
            if summary:
                lessons.append(summary[:300])
        return lessons
    except Exception:  # noqa: BLE001 — lesson recall must never break the caller
        return []


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
            stamp = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
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
            failed = getattr(episode, "outcome", "") == "failed" or any(
                term in lowered
                for term in ("rolled_back", "rollback", "failed", "rejected", "duplicate base class", "too many lines")
            )
            if self_improvement and failed:
                add(getattr(episode, "created_at", ""), text)
    except Exception:  # noqa: BLE001 — advisory history must never break CLI
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
    except Exception:  # noqa: BLE001 — malformed traces are ignored best-effort
        pass

    records.sort(key=lambda record: record["stamp"])
    return records


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


def sync_self_improvement_issue_registry(
    agent: Any,
    workspace: Path,
    *,
    max_age_days: int = 7,
) -> SelfImprovementIssueRegistry:
    """Persist recent failures and apply only explicitly matching transitions."""
    registry = SelfImprovementIssueRegistry(workspace / DEFAULT_ISSUE_PATH)
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
