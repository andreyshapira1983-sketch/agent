"""Recover completion verdicts of legacy self-build and self-repair episodes."""

from __future__ import annotations

from typing import Any

from core.writer_completion import completion_from_outcome

# Every kind the self-build writer is called with in production, read from the call
# sites; an unknown kind fails closed, so a new trigger must be added here on purpose.
_SELF_BUILD_QUESTIONS = frozenset(
    {
        "self-build-produce",
        "self-apply-run",
        "self-task-produce",
        "self-task-build",
    }
)

_SELF_BUILD_TAG = "self-build"

# Repair lessons exist only for re-verified fixes; core/self_repair.py uses the same value.
_REPAIR_VERDICT = "achieved"
_REPAIR_OUTCOME = "success"
_REPAIR_TAGS = frozenset({"bug-fix", "regression-guard"})


def _tags(payload: dict[str, Any]) -> frozenset[str]:
    """Tags as a list/tuple of str, else empty (a bare str would iterate as characters)."""
    raw = payload.get("tags")
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    if not all(isinstance(tag, str) for tag in raw):
        return frozenset()
    return frozenset(raw)


def _is_self_build_row(payload: dict[str, Any]) -> bool:
    """True only when several facts co-occur that one writer emits together."""
    question = payload.get("question")
    if question not in _SELF_BUILD_QUESTIONS:
        return False
    tags = _tags(payload)
    return _SELF_BUILD_TAG in tags and question in tags


def _is_self_repair_row(payload: dict[str, Any]) -> bool:
    """True only for a repair lesson that also stored the success it claims."""
    return (
        payload.get("goal") == "repair"
        and payload.get("outcome") == _REPAIR_OUTCOME
        and _tags(payload) >= _REPAIR_TAGS
    )


def writer_backfill_verdict(payload: dict[str, Any]) -> str | None:
    """Return the verdict a known writer would settle for ``payload``."""
    if "completion_state" in payload:
        return None
    if _is_self_repair_row(payload):
        return _REPAIR_VERDICT
    if _is_self_build_row(payload):
        return completion_from_outcome(payload.get("outcome"))
    return None
