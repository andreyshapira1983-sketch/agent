"""``:value-review`` / ``:value-review-list`` REPL commands (TD-032).

A human records whether an *applied* self-build proposal was actually valuable,
separate from the technical lane outcome. Strictly capture-only:

* Reads the approval inbox to validate eligibility; never mutates an item.
* Writes only ``data/value_reviews.jsonl`` via :class:`core.value_review.ValueReviewLog`.
* Runs no producer, no self-apply lane, no tests, no LLM, no network, no git.

Eligible items (all must hold, else a clear error and *nothing is written*):
    status    == "executed"
    operation == "self_apply_lane.run"
    payload.origin == subagent_self_build_producer
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.value_review import VALID_VERDICTS, InvalidVerdictError, ValueReviewLog

from cli.commands_approval import _approval_inbox_for

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _eligibility_error(item, item_id: str) -> str | None:
    """Return a human-readable reason the item is ineligible, or None if OK."""
    if item is None:
        return f"unknown approval id: {item_id}"
    # Lazy imports: reuse the canonical constants without a heavy/cyclic import.
    from core.self_apply_bridge import SELF_APPLY_OPERATION
    from core.self_build_producer import PRODUCER_ORIGIN

    if getattr(item, "status", None) != "executed":
        return (
            f"item {item_id} is not executed (status="
            f"{getattr(item, 'status', None)}); only applied proposals can be reviewed"
        )
    if getattr(item, "operation", None) != SELF_APPLY_OPERATION:
        return (
            f"item {item_id} operation is {getattr(item, 'operation', None)!r}; "
            f"expected {SELF_APPLY_OPERATION!r}"
        )
    payload = getattr(item, "payload", None)
    origin = payload.get("origin") if isinstance(payload, dict) else None
    if origin != PRODUCER_ORIGIN:
        return (
            f"item {item_id} origin is {origin!r}; expected {PRODUCER_ORIGIN!r} "
            "(only self-build producer proposals are value-reviewed)"
        )
    return None


def _handle_value_review(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    parts = rest.split(maxsplit=2)
    if len(parts) < 2:
        print(
            "Usage: :value-review <item_id> <verdict> [note]\n"
            f"  verdict is one of: {', '.join(sorted(VALID_VERDICTS))}",
            file=sys.stderr,
        )
        return True
    item_id, verdict = parts[0], parts[1]
    note = parts[2] if len(parts) == 3 else ""

    # Validate the verdict before touching anything else; write nothing on error.
    if verdict not in VALID_VERDICTS:
        print(
            f"(value-review rejected: invalid verdict {verdict!r}; expected one of "
            f"{', '.join(sorted(VALID_VERDICTS))})",
            file=sys.stderr,
        )
        return True

    inbox = _approval_inbox_for(agent, workspace)
    item = inbox.get(item_id)
    reason = _eligibility_error(item, item_id)
    if reason is not None:
        print(f"(value-review rejected: {reason})", file=sys.stderr)
        return True

    log = ValueReviewLog.for_workspace(workspace)
    try:
        review = log.append(item_id, verdict, note=note)
    except InvalidVerdictError as exc:  # defensive; verdict already validated
        print(f"(value-review rejected: {exc})", file=sys.stderr)
        return True

    agent.log.log(
        "value_review_recorded",
        {"item_id": review.item_id, "verdict": review.verdict},
    )
    # TD-033: best-effort project the latest verdicts onto subagent scoring.
    # Guarded so a registry read/write failure never breaks value-review capture.
    try:
        from core.subagent_registry import SubagentRegistry

        registry = SubagentRegistry.load(workspace)
        effective = {
            item_id: review.verdict
            for item_id, review in log.effective_by_item_id().items()
        }
        registry.reconcile_value_reviews(effective)
    except Exception:  # noqa: BLE001 — scoring is advisory; persistence must win
        pass
    print(
        f"(value-review recorded: {review.item_id} -> {review.verdict})",
        file=sys.stderr,
    )
    return True


def _handle_value_review_list(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    """Read-only: eligible applied producer proposals and their effective verdict."""
    from core.self_apply_bridge import SELF_APPLY_OPERATION
    from core.self_build_producer import PRODUCER_ORIGIN

    inbox = _approval_inbox_for(agent, workspace)
    executed = inbox.list(status="executed")
    eligible = [
        it
        for it in executed
        if getattr(it, "operation", None) == SELF_APPLY_OPERATION
        and isinstance(getattr(it, "payload", None), dict)
        and it.payload.get("origin") == PRODUCER_ORIGIN
    ]

    effective = ValueReviewLog.for_workspace(workspace).effective_by_item_id()

    if not eligible:
        print("(no applied self-build proposals to value-review)", file=sys.stderr)
        return True

    print(f"=== value-review queue ({len(eligible)}) ===", file=sys.stderr)
    for it in eligible:
        review = effective.get(it.id)
        verdict = review.verdict if review is not None else "(no verdict yet)"
        print(f"  {it.id} -> {verdict}  summary={it.summary}", file=sys.stderr)
        if review is not None and review.note:
            print(f"    note={review.note}", file=sys.stderr)
    return True
