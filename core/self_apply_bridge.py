"""Approval -> trusted self-apply lane bridge: runs a persisted, validated inbox proposal through the lane (never auto-triggered, no free-text patches, no network)."""
# Only terminal lane statuses (committed_local / rolled_back) mark the inbox item executed; transient refusals leave it approved for retry.
from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.directive_extractor import SourceText, extract
from core.instruction_conflict_gate import AUTHORITY_RANK, Directive
from core.self_apply_lane import (
    FileChange,
    SelfApplyProposal,
    SelfApplyReport,
    classify_patch_risk,
    file_base_sha256,
    run_self_apply_lane,
)

logger = logging.getLogger(__name__)

# Only inbox items with exactly this operation may be routed through the lane.
SELF_APPLY_OPERATION = "self_apply_lane.run"

# Terminal lane statuses that consume the approval; transient refusals are excluded so the item can be retried.
TERMINAL_LANE_STATUSES = frozenset({"committed_local", "rolled_back"})

# Rollback is built into the lane (reset_hard + delete temp branch); a proposal records this rather than a script.
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
    workspace: Path | None = None,
) -> dict:
    """Build a well-formed self_apply_lane.run inbox payload; each file needs a non-empty path and full content (diff-only is rejected)."""
    normalized = _stamp_base_state(_normalize_files(files), workspace)
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


def _stamp_base_state(files: list[dict], workspace: Path | None) -> list[dict]:
    """Record what the proposal is built on while the pre-image is at hand; without a workspace no mark is set ("not looked at" differs from "matched")."""
    if workspace is None:
        return files
    root = Path(workspace)
    for entry in files:
        target = root / str(entry["path"])
        try:
            before = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # No file (or not text): an empty hash with the flag raised means "the file did not exist then".
            entry["base_sha256"] = ""
        else:
            entry["base_sha256"] = file_base_sha256(before)
        entry["base_checked"] = True
    return files


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
        # File content is SOURCE CODE written verbatim, but the durable inbox runs payloads through the DLP redactor, which would corrupt
        # code containing PII-shaped text; a redaction-inert base64 copy (content_b64) is authoritative when present.
        content: str | None = None
        b64 = entry.get("content_b64")
        if isinstance(b64, str) and b64.strip():
            try:
                content = base64.b64decode(b64.encode("ascii")).decode("utf-8")
            except Exception:  # noqa: BLE001 — fall back to the plaintext field
                content = None
        if content is None:
            if "content" not in entry:
                # Diff-only payloads are rejected: the lane overwrites whole files and needs the full post-image.
                if "diff" in entry:
                    raise InvalidProposalError(
                        f"diff-only change for {path!r} is not supported; full "
                        "'content' is required"
                    )
                raise InvalidProposalError(
                    f"file change for {path!r} missing 'content'"
                )
            content = entry.get("content")
            if not isinstance(content, str):
                raise InvalidProposalError(
                    f"file change for {path!r} must carry string 'content'"
                )
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        change = {"path": path, "content": content, "content_b64": encoded}
        # The state mark is carried over, not rebuilt: rebuilding would hash TODAY's file and declare a match nobody checked.
        if entry.get("base_checked"):
            change["base_sha256"] = str(entry.get("base_sha256") or "")
            change["base_checked"] = True
        out.append(change)
    return out


def rehydrate_proposal(payload: Any) -> SelfApplyProposal:
    """Rebuild a SelfApplyProposal from a persisted payload; raises InvalidProposalError on missing fields or diff-only changes."""
    if not isinstance(payload, dict):
        raise InvalidProposalError("proposal payload must be an object")
    normalized = _normalize_files(payload.get("files"))
    changes = tuple(
        FileChange(
            path=f["path"],
            content=f["content"],
            base_sha256=str(f.get("base_sha256") or ""),
            base_checked=bool(f.get("base_checked")),
        )
        for f in normalized
    )
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
        directives=_directives_from_payload(payload),
    )


#: Authority levels a payload may claim; operator is excluded because only a human channel confers it.
_PAYLOAD_DECLARABLE_LEVELS = frozenset(AUTHORITY_RANK) - {"operator"}


def _directives_from_payload(payload: dict) -> tuple[Directive, ...]:
    """Recover the requirements this patch answers, with their authority.

    Channels: reason (read as the task contract) and instructions (list of {level, source, text, locator}). A payload may not claim
    operator authority; any level outside the declarable set is demoted to advisor.
    """
    sources: list[SourceText] = []

    reason = str(payload.get("reason") or "")
    if reason.strip():
        sources.append(SourceText(
            text=reason,
            source_level="task_contract",
            source_name="обоснование заявки",
        ))

    raw = payload.get("instructions")
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "")
            if not text.strip():
                continue
            level = str(entry.get("level") or "")
            if level not in _PAYLOAD_DECLARABLE_LEVELS:
                level = "advisor"
            sources.append(SourceText(
                text=text,
                source_level=level,  # type: ignore[arg-type]
                source_name=str(entry.get("source") or level),
                locator=str(entry.get("locator") or ""),
            ))

    return extract(sources)


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
    except Exception:  # noqa: BLE001 — pragma: no cover - inbox contract is trusted
        return 0
    return sum(1 for it in pending if getattr(it, "id", None) != item_id)


def run_approved_self_apply(  # noqa: PLR0913 — flat: depth 1, all 9 returns are guard clauses
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
    registry: Any = None,
    gateway: Any = None,
    dry_run: bool = False,
) -> dict:
    """Route one approved inbox item through the trusted self-apply lane."""
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

    # Gateway G3: single actuation door before ANY local mutation / SafeVCS op; adds the run-mode decision (simulate under dry-run). Fail closed on error.
    gw = _gateway_for(
        gateway,
        dry_run,
        kill_switch=kill_switch,
        budget_snapshot=budget_snapshot,
        check_readiness=False,
    )
    try:
        decision = gw.evaluate_self_apply(operation=SELF_APPLY_OPERATION)
        outcome = getattr(decision, "outcome", "deny")
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # Fail closed; the best-effort G4 error receipt must never affect that outcome.
        _record_self_apply_gateway_error(workspace, item_id)
        return _refusal(
            proposal_id=item_id,
            status="gateway_error",
            reason=(
                f"actuation gateway error (fail-closed): {type(exc).__name__}: {exc}"
            ),
            next_human_action="No changes were applied; resolve the gateway error and retry.",
        )

    # G4: durable gateway decision receipt; observation only, never raises.
    try:
        from core.tool_receipts import record_gateway_receipt

        record_gateway_receipt(
            decision, workspace=workspace, refs={"proposal_id": item_id}
        )
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # A receipt missing from the audit trail of an effectful operation is the gap the trail exists to close.
        logger.warning(
            "self-apply gateway receipt not written for %s: %s: %s",
            item_id, type(exc).__name__, exc,
        )

    if outcome == "simulate":
        return _refusal(
            proposal_id=item_id,
            status="simulated",
            reason="gateway dry_run: self-apply simulated; no files changed",
            next_human_action="Re-run without dry-run to apply the approved proposal.",
        )
    if outcome != "allow":
        return _refusal(
            proposal_id=item_id,
            status="gateway_blocked",
            reason=f"actuation gateway outcome={outcome!r}; no mutation performed",
            next_human_action="Resolve the gateway decision before applying.",
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

    # Only terminal statuses consume the approval. A commit is executed; a rollback is aborted, so the value-review queue never asks a human to grade a discarded change.
    if report.status in TERMINAL_LANE_STATUSES:
        try:
            (inbox.mark_executed if report.status == "committed_local" else inbox.abort)(item_id)
        except (KeyError, ValueError) as exc:  # item expired or vanished while the lane ran
            _note_unconsumed_approval(result, item_id, exc)
        _record_lane_outcome(registry, item, report.status)

    return result


def _note_unconsumed_approval(result: dict, item_id: str, exc: Exception) -> None:
    """Put the inbox mismatch where the operator reads it: the log and `risks`."""
    logger.warning("approval %s not consumed: %s", item_id, exc)
    note = f"approval_not_consumed: lane ended {result.get('status')}, but {exc}"
    result["risks"] = [*(result.get("risks") or ()), note]


def _record_self_apply_gateway_error(workspace: Path, item_id: str) -> None:
    """Best-effort G4 receipt for a gateway exception; never raises, never blocks the fail-closed path."""
    try:
        from core.actuation_gateway import GatewayDecision
        from core.tool_receipts import record_gateway_receipt

        decision = GatewayDecision(
            outcome="deny", tool_name=SELF_APPLY_OPERATION, path="self_apply",
            reasons=("gateway raised; fail-closed",),
        )
        record_gateway_receipt(
            decision,
            workspace=workspace,
            status="error",
            refs={"proposal_id": item_id},
        )
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # This IS the fail-closed path: failing silently here leaves a refused effectful operation with no record it was attempted.
        logger.warning(
            "fail-closed gateway receipt not written for %s: %s: %s",
            item_id, type(exc).__name__, exc,
        )


def _gateway_for(
    gateway: Any,
    dry_run: bool,
    *,
    kill_switch: Any | None = None,
    budget_snapshot: dict | None = None,
    readiness_blockers: tuple[str, ...] = (),
    check_readiness: bool = False,
) -> Any:
    """Return the injected gateway or build a default self-apply ActuationGateway."""
    if gateway is not None:
        return gateway
    from core.actuation_gateway import ActuationGateway  # local import: avoid cycles

    return ActuationGateway(
        policy=None,
        path="self_apply",
        dry_run=dry_run,
        kill_switch=kill_switch,
        budget_snapshot=budget_snapshot,
        readiness_blockers=readiness_blockers,
        check_readiness=check_readiness,
    )


def _record_lane_outcome(registry: Any, item: Any, status: str) -> None:
    """Best-effort ledger recording for a terminal lane outcome; only producer-origin self_apply_lane.run items, registry write failures swallowed."""
    if registry is None:
        return
    if status not in TERMINAL_LANE_STATUSES:
        return
    if getattr(item, "operation", None) != SELF_APPLY_OPERATION:
        return
    try:
        # Lazy import avoids a circular dependency (producer imports this module).
        from core.self_build_producer import PRODUCER_ORIGIN
        if _origin_of(item) != PRODUCER_ORIGIN:
            return
        registry.apply_lane_outcome(getattr(item, "id", None), status)
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # The lane outcome stops the producer re-proposing the same target; losing it silently means re-proposing applied or refused work.
        logger.warning(
            "self-apply lane outcome not recorded for %s: %s: %s",
            getattr(item, "id", "?"), type(exc).__name__, exc,
        )


def _origin_of(item: Any) -> str:
    payload = getattr(item, "payload", None)
    if isinstance(payload, dict):
        return str(payload.get("origin") or "unknown")
    return "unknown"
