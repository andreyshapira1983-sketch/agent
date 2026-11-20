"""Apply what a RULE, not a human, permits: the loop for one narrow product class, new documents."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class _Authority:
    """Who authorises this apply, what limits it and what it lets through.

        Two authorities: the rule ("new documents only", under a standing operator grant) and the sandbox (code inside an isolated copy, under explicit burn-in authority). One shape on purpose, so the drain body does not branch on authority."""

    id: str
    actor: str
    verdict: Callable[[Any], tuple[bool, str]]
    #: Reserve one unit of the ceiling. True = reserved, may apply; False = nothing left. Decision and record are inseparable: ask-then-record let two concurrent drains through.
    reserve: Callable[[], bool]
    #: Snapshot of the remainder on entry, for fast refusal and the journal only; the truth lives in `reserve`.
    remaining: int
    sandbox: bool


#: Default sink set for a drain. Not `None`: `None` on the ladder means a human at the keyboard with no limits, and a drain is unattended, so a caller that forgets the profile gets the narrowest answer.
_UNATTENDED: frozenset[str] = frozenset()


def _authority_for(
    workspace: Path, inbox: Any, *, env: Any, say: Callable[[str, dict], None]
) -> _Authority | None:
    """The acting authority, or None if nobody can permit it.

        The sandbox is asked first; without its authority enabled the function behaves as before: standing grant or refusal."""
    from core.autonomous_runtime import (
        active_standing_grant,
        reserve_standing_grant_use,
        standing_grant_remaining_runs,
    )
    from core.burn_in_sandbox import (
        load_sandbox_authority,
        reserve_sandbox_apply,
        sandbox_applies_today,
        sandbox_execution_verdict,
    )
    from core.self_apply_lane import autonomous_execution_verdict

    sandbox = load_sandbox_authority(workspace, env=env)
    if sandbox is not None:
        say("sandbox_authority_active", sandbox.to_dict())
        return _Authority(
            id=sandbox.id,
            actor="sandbox:burn_in",
            verdict=lambda p: sandbox_execution_verdict(p, workspace=workspace),
            reserve=lambda: reserve_sandbox_apply(workspace, sandbox),
            remaining=sandbox.max_applies_per_day - sandbox_applies_today(workspace, sandbox),
            sandbox=True,
        )
    grant = active_standing_grant(inbox, workspace)
    if grant is None:
        return None
    grant_id = getattr(grant, "id", "")
    grant_cap = int((getattr(grant, "payload", None) or {}).get("max_runs_per_day") or 0)
    return _Authority(
        id=grant_id,
        actor="rule:documents_only",
        verdict=autonomous_execution_verdict,
        reserve=lambda: reserve_standing_grant_use(
            workspace, grant_id, max_per_day=grant_cap
        ),
        # Remainder for today, not the ceiling: the spend journal is shared with the runtime, so a rule-applied apply must count against the daily runs.
        remaining=standing_grant_remaining_runs(workspace, grant),
        sandbox=False,
    )


def _authorised_candidates(inbox: Any, authority: _Authority) -> list[Any]:
    """Retry only this sandbox's approvals; a human's approval is not its queue."""
    candidates = list(inbox.pending())
    if authority.sandbox:
        candidates += [
            item for item in inbox.list(status="approved")
            if item.decided_by == authority.actor
        ]
    return candidates


def stale_proposal_files(workspace, proposal) -> list[str]:
    """Request paths whose file on disk is already newer than the request, checked BEFORE spending authority.

        Same rule as the lane (`core.self_apply_lane._stale_changes`); otherwise a stale proposal burns grant units doing nothing."""
    from core.self_apply_lane import _stale_changes

    return _stale_changes(Path(workspace), getattr(proposal, "files", ()))


def drain_rule_approved_proposals(
    workspace: Path,
    *,
    dry_run: bool,
    log: Callable[[str, dict], None] | None = None,
    env: Any = None,
    durable_writes: Any = _UNATTENDED,
) -> dict:
    """Apply proposals that a rule permits without a human.

        Two gates before any action: effects enabled AND the standing grant valid, the latter asked by the SAME function the runtime uses. Request, permission and execution stay three events: the permission is recorded on behalf of the rule, execution is logged separately. The sandbox authority (`core/burn_in_sandbox.py`) changes only WHO permits and which file class passes; dryness, the apply lane, its tests, branch, rollback and the no-push rule are unchanged. Not enabled: the production path runs unchanged."""
    from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
    from core.safe_vcs import SafeVCS
    from core.self_apply_bridge import (
        SELF_APPLY_OPERATION,
        TERMINAL_LANE_STATUSES,
        rehydrate_proposal,
        run_approved_self_apply,
    )
    from core.self_build_rules import blocking_lesson, record_lessons_from_result
    from tools.run_tests import RunTestsTool

    def _say(event: str, payload: dict) -> None:
        if log is not None:
            log(event, payload)

    out: dict[str, Any] = {
        "considered": 0, "applied": 0, "unapplied": 0, "attempted": 0,
        "refused": 0, "blocked": "",
    }
    if dry_run:
        out["blocked"] = "effects disabled"
        return out
    inbox = ApprovalInbox(path=Path(workspace) / DEFAULT_APPROVAL_INBOX_PATH)

    # Authority is asked AFTER dryness: an experiment may widen the change class but not cancel "change nothing".
    authority = _authority_for(workspace, inbox, env=env, say=_say)
    if authority is None:
        out["blocked"] = "no active standing grant"
        return out
    remaining = authority.remaining
    # The authority names itself: an exhausted sandbox must not refuse with the words "standing grant", a permission of another nature.
    spent_word = "sandbox daily cap reached" if authority.sandbox else "standing grant spent for today"
    spent_event = "sandbox_cap_exhausted" if authority.sandbox else "standing_grant_exhausted"

    for item in _authorised_candidates(inbox, authority):
        if getattr(item, "operation", "") != SELF_APPLY_OPERATION:
            continue
        out["considered"] += 1
        if remaining <= 0:
            out["refused"] += 1
            out["blocked"] = spent_word
            _say(spent_event, {
                "approval_id": item.id, "grant_id": authority.id,
            })
            continue
        try:
            proposal = rehydrate_proposal(item.payload)
        except Exception:  # noqa: BLE001 — негодная заявка не вправе ронять тик
            out["refused"] += 1
            continue
        allowed, reason = authority.verdict(proposal)
        if not allowed:
            out["refused"] += 1
            _say("rule_approval_refused", {"approval_id": item.id, "reason": reason})
            continue
        # Past attempts are a gate, not an archive: a rollback at this address already happened, so the automaton does not repeat a check it failed once; the request stays in the inbox for a human.
        targets = [getattr(f, "path", "") for f in getattr(proposal, "files", ())]
        lesson = blocking_lesson(workspace, targets)
        if lesson is not None:
            out["refused"] += 1
            out["blocked"] = out["blocked"] or "prior rollback lesson"
            _say("rule_approval_refused_by_lesson", {
                "approval_id": item.id,
                "scope": list(lesson.scope),
                "failure": lesson.failure[:200],
                "learned_at": lesson.created_at,
            })
            continue
        stale = stale_proposal_files(workspace, proposal)
        if stale:
            out["refused"] += 1
            out["blocked"] = out["blocked"] or "stale proposal"
            _say("rule_approval_refused_stale", {
                "approval_id": item.id, "stale_files": stale,
                "next_human_action": "rebuild the proposal against the current file",
            })
            continue
        # Spend is reserved BEFORE applying and inseparably from the remainder check: an interrupted tick leaves an over-estimated spend, and two concurrent drains cannot spend the same last unit.
        if not authority.reserve():
            out["refused"] += 1
            out["blocked"] = spent_word
            _say(spent_event, {
                "approval_id": item.id, "grant_id": authority.id,
            })
            continue
        remaining -= 1
        inbox.approve(item.id, reason=reason, actor=authority.actor)
        result = run_approved_self_apply(
            inbox=inbox,
            item_id=item.id,
            workspace=Path(workspace),
            vcs=SafeVCS(workspace=Path(workspace)),
            test_runner=RunTestsTool(workspace_root=Path(workspace)),
            # Sandbox authority is per proposal, not a veto from unrelated requests.
            approvals_pending=0 if authority.sandbox else None,
        )
        out["attempted"] += 1
        status = str(result.get("status") or "")
        # `applied` counts what was APPLIED, not started; authority spend stays spent (the attempt cost a battery run) and `attempted` holds the honest total.
        if status == "committed_local":
            out["applied"] += 1
        else:
            out["unapplied"] += 1
        # A non-terminal outcome is about the state of the WORLD (approval queue, budget, gateway), not the request: the next request would hit the same wall and take another unit. The request stays approved; the sandbox repeats its decision at the next drain. A production decision is still repeated by the operator.
        if status not in TERMINAL_LANE_STATUSES:
            out["blocked"] = out["blocked"] or f"lane refused: {status or 'unknown'}"
            _say("rule_approved_lane_wall", {
                "approval_id": item.id,
                "grant_id": authority.id,
                "status": status,
                "reason": str(result.get("reason") or "")[:200],
                "grant_runs_left": remaining,
            })
            break
        # A verified candidate is OFFERED to the adopter, not adopted: offering grants no rights and does not move the experience head; it only narrows what `core/burn_in_supervisor.adopt_offer` will consider. Sandbox only.
        if authority.sandbox and status == "committed_local":
            _offer_to_supervisor(workspace, result, item_id=item.id, log=_say)
        # The outcome becomes knowledge HERE, without a human command (lessons used to be written only by `cli/commands_self_apply.py`).
        learned = record_lessons_from_result(
            workspace, result,
            origin="burn_in_sandbox" if authority.sandbox else "rule_approved_apply",
            reason=getattr(proposal, "reason", ""),
            # Same sink set as the agent's build: a lesson is a durable record and passes the same gates.
            durable_writes=durable_writes,
            log=log,
        )
        _say("rule_approved_applied", {
            "approval_id": item.id,
            "grant_id": authority.id,
            "status": result.get("status"),
            "reason": reason,
            "grant_runs_left": remaining,
            "lessons_recorded": learned.get("lessons", 0),
            "rules_recorded": learned.get("rules", 0),
        })
    return out


def _offer_to_supervisor(
    workspace: Path, result: dict, *, item_id: str, log: Callable[[str, dict], None]
) -> None:
    """Put the SHA of the verified candidate into the offers registry.

        Best-effort: a registry failure must not crash the apply, but must not pass silently either."""
    sha = str(result.get("commit_hash") or "")
    try:
        from core.burn_in_supervisor import offer_verified_commit

        taken = offer_verified_commit(
            Path(workspace), sha=sha, proposal_id=item_id,
            tests_run=result.get("tests_run") or (),
            reason=str(result.get("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — реестр не роняет применение
        log("burn_in_offer_failed", {"approval_id": item_id,
                                     "error": type(exc).__name__})
        return
    log("burn_in_offer" if taken else "burn_in_offer_rejected",
        {"approval_id": item_id, "sha": sha})


def drain_and_log(
    workspace: Path,
    *,
    dry_run: bool,
    log_tick: Callable[[dict], None],
    durable_writes: Any = _UNATTENDED,
) -> None:
    """Wrapper for the live campaign path: the trace is ALWAYS written, errors are swallowed.

        The first wiring sat at the tail of `run_tick`, which the scheduled task does not reach (`--campaign` exits through `run_paced_campaign`)."""
    try:
        drain = drain_rule_approved_proposals(
            workspace, dry_run=dry_run,
            log=lambda e, p: log_tick({"event": e, **p}),
            durable_writes=durable_writes,
        )
        log_tick({"event": "rule_approved_drain", **drain})
    except Exception as exc:  # noqa: BLE001 — петля не вправе ронять кампанию
        log_tick({"event": "rule_approval_error", "error": type(exc).__name__})
