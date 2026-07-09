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

from typing import Any

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
