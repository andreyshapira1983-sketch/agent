"""Approval -> trusted self-apply lane bridge (TD-024).

This is the *narrow* connection between the human approval surface and the
trusted low-risk apply lane (TD-023, :mod:`core.self_apply_lane`). It does one
thing only: take an approval-inbox item that a human has already approved,
rehydrate the validated structured proposal it carries, and route it through
``run_self_apply_lane``.

It is deliberately *not*:

  * a daemon / scheduler / agent_tick hook (never auto-triggered),
  * a free-text patch executor (only a persisted, validated proposal is run),
  * a widening of ``shell_exec`` or the network surface (the lane still goes
    exclusively through :class:`core.safe_vcs.SafeVCS`, which has no push /
    fetch / pull / remote method at all).

Gate order in :func:`run_approved_self_apply` (first trip wins, all *before*
any file is touched):

  1. item missing / not approved            -> status="approval_required"
  2. wrong operation / invalid payload       -> status="needs_validated_proposal"
  3. patch not low-risk                       -> status="risk_rejected"
  4. otherwise -> run_self_apply_lane exactly once; its status is surfaced
     unchanged (budget_kill_switch / budget_wait / approval_wait / rejected /
     rolled_back / committed_local / error).

Only terminal lane statuses (``committed_local`` / ``rolled_back``) mark the
inbox item executed; transient refusals (``budget_kill_switch`` /
``budget_wait`` / ``approval_wait``) leave it approved so it can be retried.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.self_apply_lane import (
    FileChange,
    SelfApplyProposal,
    SelfApplyReport,
    classify_patch_risk,
    run_self_apply_lane,
)

# Only approval-inbox items carrying exactly this operation may be routed
# through the self-apply lane. Anything else is refused.
SELF_APPLY_OPERATION = "self_apply_lane.run"

# Lane statuses that terminally consume the approval (mark_executed). Transient
# refusals are intentionally excluded so the operator can retry the same item.
TERMINAL_LANE_STATUSES = frozenset({"committed_local", "rolled_back"})

# Self-apply rollback is built into the lane (reset_hard + delete temp branch);
# a proposal records this rather than an ad-hoc script.
DEFAULT_ROLLBACK = "self_apply_lane:reset_hard+delete_temp_branch"

# Signature of the lane callable so tests can inject a fake.
LaneFn = Callable[..., SelfApplyReport]


class InvalidProposalError(ValueError):
    """Raised when an inbox payload cannot yield a valid SelfApplyProposal."""


def build_self_apply_payload(
    *,
    files: list[dict] | tuple[dict, ...],
    reason: str = "",
    evidence: list[str] | tuple[str, ...] = (),
    test_paths: list[str] | tuple[str, ...] = ("tests",),
    test_pattern: str | None = None,
    origin: str = "manual",
    rollback: str = DEFAULT_ROLLBACK,
) -> dict:
    """Build a well-formed ``self_apply_lane.run`` inbox payload.

    Each file entry must carry a non-empty ``path`` and full ``content`` (a
    diff-only entry is rejected). Validating here means producers
    (repair / supervisor / manual) and the runtime agree on one shape.
    """
    normalized = _normalize_files(files)
    payload: dict[str, Any] = {
        "files": normalized,
        "reason": str(reason or ""),
        "evidence": [str(e) for e in (evidence or ())],
        "test_paths": [str(p) for p in (test_paths or ("tests",))],
        "test_pattern": test_pattern,
        "origin": str(origin or "manual"),
        "rollback": str(rollback or DEFAULT_ROLLBACK),
    }
    return payload


def _normalize_files(files: Any) -> list[dict]:
    if not isinstance(files, (list, tuple)) or not files:
        raise InvalidProposalError("proposal must list at least one file change")
    out: list[dict] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise InvalidProposalError("each file change must be an object")
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            raise InvalidProposalError("file change missing a non-empty 'path'")
        if "content" not in entry:
            # Explicitly reject diff-only payloads: the lane overwrites whole
            # files, so it needs the full post-image, never a unified diff.
            if "diff" in entry:
                raise InvalidProposalError(
                    f"diff-only change for {path!r} is not supported; full "
                    "'content' is required"
                )
            raise InvalidProposalError(f"file change for {path!r} missing 'content'")
        content = entry.get("content")
        if not isinstance(content, str):
            raise InvalidProposalError(
                f"file change for {path!r} must carry string 'content'"
            )
        out.append({"path": path, "content": content})
    return out


def rehydrate_proposal(payload: Any) -> SelfApplyProposal:
    """Rebuild a :class:`SelfApplyProposal` from a persisted inbox payload.

    Raises :class:`InvalidProposalError` when required fields are missing or a
    file change is diff-only rather than full-content.
    """
    if not isinstance(payload, dict):
        raise InvalidProposalError("proposal payload must be an object")
    normalized = _normalize_files(payload.get("files"))
    changes = tuple(FileChange(path=f["path"], content=f["content"]) for f in normalized)
    test_paths_raw = payload.get("test_paths") or ("tests",)
    if not isinstance(test_paths_raw, (list, tuple)) or not test_paths_raw:
        raise InvalidProposalError("proposal 'test_paths' must be a non-empty list")
    test_pattern = payload.get("test_pattern")
    if test_pattern is not None and not isinstance(test_pattern, str):
        raise InvalidProposalError("proposal 'test_pattern' must be a string or null")
    evidence = payload.get("evidence") or ()
    if not isinstance(evidence, (list, tuple)):
        evidence = (str(evidence),)
    return SelfApplyProposal(
        files=changes,
        reason=str(payload.get("reason") or ""),
        evidence=tuple(str(e) for e in evidence),
        test_paths=tuple(str(p) for p in test_paths_raw),
        test_pattern=test_pattern,
    )


def _refusal(
    *, proposal_id: str, status: str, reason: str, next_human_action: str, **extra: Any
) -> dict:
    result = {
        "proposal_id": proposal_id,
        "status": status,
        "reason": reason,
        "branch": None,
        "files_changed": [],
        "tests_run": [],
        "rollback_status": "none",
        "commit_hash": None,
        "rejected_files": [],
        "risks": [],
        "next_human_action": next_human_action,
    }
    result.update(extra)
    return result


def _pending_excluding(inbox: Any, item_id: str) -> int:
    """Count *other* pending approvals — never the item being executed."""
    try:
        pending = inbox.pending()
    except Exception:  # pragma: no cover - inbox contract is trusted
        return 0
    return sum(1 for it in pending if getattr(it, "id", None) != item_id)


def run_approved_self_apply(
    *,
    inbox: Any,
    item_id: str,
    workspace: Path,
    vcs: Any,
    test_runner: Any,
    kill_switch: Any | None = None,
    budget_snapshot: dict | None = None,
    approvals_pending: int | None = None,
    now_iso: str | None = None,
    lane: LaneFn = run_self_apply_lane,
) -> dict:
    """Route one approved inbox item through the trusted self-apply lane.

    Returns a normalized result dict (always includes ``proposal_id`` and the
    lane report fields). Marks the item executed only on a terminal lane
    status. ``lane`` is injectable purely so tests can substitute a fake.
    """
    item = inbox.get(item_id)
    if item is None:
        return _refusal(
            proposal_id=item_id,
            status="needs_validated_proposal",
            reason=f"approval not found: {item_id}",
            next_human_action="Check :approval-list for a valid approved id.",
        )

    # Gate 1: must be human-approved.
    if getattr(item, "status", None) != "approved":
        return _refusal(
            proposal_id=item_id,
            status="approval_required",
            reason=f"item status={getattr(item, 'status', None)}; approve it first",
            next_human_action=f"Approve it first: :approval-approve {item_id}",
        )

    # Gate 2a: only the self-apply operation is accepted here.
    if getattr(item, "operation", None) != SELF_APPLY_OPERATION:
        return _refusal(
            proposal_id=item_id,
            status="needs_validated_proposal",
            reason=(
                f"unsupported operation={getattr(item, 'operation', None)!r}; "
                f"expected {SELF_APPLY_OPERATION!r}"
            ),
            next_human_action=(
                "Route this item through its own handler; :self-apply-run only "
                f"executes {SELF_APPLY_OPERATION} proposals."
            ),
        )

    # Gate 2b: payload must yield a valid full-content proposal.
    try:
        proposal = rehydrate_proposal(getattr(item, "payload", None))
    except InvalidProposalError as exc:
        return _refusal(
            proposal_id=item_id,
            status="needs_validated_proposal",
            reason=f"invalid proposal payload: {exc}",
            next_human_action=(
                "Recreate the proposal with full file content and required "
                "fields (files, test_paths)."
            ),
        )

    # Gate 3: low-risk classification (same classifier the lane uses).
    ok, risk_reason, rejected = classify_patch_risk(proposal.files)
    if not ok:
        return _refusal(
            proposal_id=item_id,
            status="risk_rejected",
            reason=risk_reason,
            rejected_files=list(rejected),
            risks=[risk_reason],
            next_human_action=(
                "Narrow the patch to allowlisted source/test/docs files, or "
                "route sensitive changes through explicit human approval."
            ),
        )

    if approvals_pending is None:
        approvals_pending = _pending_excluding(inbox, item_id)

    report = lane(
        proposal,
        workspace=workspace,
        vcs=vcs,
        test_runner=test_runner,
        budget_snapshot=budget_snapshot,
        approvals_pending=approvals_pending,
        kill_switch=kill_switch,
        now_iso=now_iso,
    )

    result = {"proposal_id": item_id, "origin": _origin_of(item), **report.to_dict()}

    # Only terminal statuses consume the approval; transient refusals leave the
    # item approved so the operator can retry once budget/queue recovers.
    if report.status in TERMINAL_LANE_STATUSES:
        inbox.mark_executed(item_id)

    return result


def _origin_of(item: Any) -> str:
    payload = getattr(item, "payload", None)
    if isinstance(payload, dict):
        return str(payload.get("origin") or "unknown")
    return "unknown"
