"""Conversational routing for operator-control requests.

The CLI still exposes explicit `:commands`, but the long-term operator UX
should let the owner ask normal status questions. This module keeps that
translation deterministic and local, so common control-plane requests do not
need an LLM call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .operator_intent_patterns import (
    _explicit_documentation_requested,
    _has_any,
    _is_explicit_budget_status_command,
    _looks_like_engineering_change_request,
    _looks_like_explicit_non_routing_command,
    _looks_like_meta_instruction,
    _looks_like_plain_bug_note,
    _looks_like_self_build_request,
    _looks_like_shell_command,
    _matches_approval_status,
    _matches_autonomy_readiness,
    _matches_best_next_action,
    _matches_budget_status,
    _matches_capability_check,
    _matches_capability_request,
    _matches_current_gaps_check,
    _matches_implementation_plan,
    _matches_inbox_task_request,
    _matches_model_status,
    _matches_next_actions,
    _matches_next_safe_test,
    _matches_patch_proposal,
    _matches_programming_readiness,
    _matches_project_health,
    _matches_safe_self_check,
    _matches_self_task_propose,
    _matches_smart_memory_status,
    _matches_source_review,
    _matches_urgent_status,
    _matches_weakness_finder,
)


OperatorIntentKind = Literal[
    "shell_command_hint",
    "capability_request",
    "self_task_proposal",
    "safe_self_check",
    "capability_check",
    "programming_readiness",
    "current_gaps_check",
    "weakness_finder",
    "next_safe_test",
    "project_health",
    "smart_memory_status",
    "model_status",
    "budget_status",
    "approval_status",
    "urgent_status",
    "best_next_action",
    "next_actions",
    "autonomy_readiness",
    "source_review_plan",
    "implementation_plan",
    "patch_proposal",
]


@dataclass(frozen=True)
class OperatorIntent:
    kind: OperatorIntentKind
    command: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "command": self.command,
            "reason": self.reason,
        }


def route_operator_intent(text: str) -> OperatorIntent | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if len(normalized) > 600:
        return None
    if _looks_like_meta_instruction(normalized):
        return None
    if _looks_like_plain_bug_note(normalized):
        return None
    if _looks_like_explicit_non_routing_command(normalized):
        return None
    if _looks_like_self_build_request(normalized):
        return None
    if _matches_inbox_task_request(normalized):
        return None
    if _looks_like_shell_command(normalized):
        return OperatorIntent(
            kind="shell_command_hint",
            command="shell-command-hint",
            reason="shell/powershell command wording",
        )
    if _matches_capability_request(normalized):
        return OperatorIntent(
            kind="capability_request",
            command=":capability-request",
            reason="missing capability / connector proposal wording",
        )
    if _matches_self_task_propose(normalized):
        return OperatorIntent(
            kind="self_task_proposal",
            command=":self-task-propose",
            reason="TODO/FIXME + propose-task/failing-test wording",
        )
    if _matches_patch_proposal(normalized):
        return OperatorIntent(
            kind="patch_proposal",
            command=":patch-proposal-plan",
            reason="patch proposal wording",
        )
    if _matches_source_review(normalized):
        return OperatorIntent(
            kind="source_review_plan",
            command=":source-review-plan",
            reason="implementation/source-review planning wording",
        )
    if _matches_implementation_plan(normalized):
        return OperatorIntent(
            kind="implementation_plan",
            command=":implementation-plan",
            reason="implementation planning wording",
        )
    if _explicit_documentation_requested(normalized):
        return None
    if _matches_safe_self_check(normalized):
        return OperatorIntent(
            kind="safe_self_check",
            command=":operator-check",
            reason="safe self-check wording",
        )
    if _matches_programming_readiness(normalized):
        return OperatorIntent(
            kind="programming_readiness",
            command=":coding-readiness",
            reason="safe programming readiness wording",
        )
    if _matches_capability_check(normalized):
        return OperatorIntent(
            kind="capability_check",
            command="operator-capabilities",
            reason="capability/status wording",
        )
    if _matches_current_gaps_check(normalized):
        return OperatorIntent(
            kind="current_gaps_check",
            command="operator-gaps",
            reason="current gaps wording",
        )
    if _matches_weakness_finder(normalized):
        return OperatorIntent(
            kind="weakness_finder",
            command="operator-weaknesses",
            reason="live weakness wording",
        )
    if _matches_next_safe_test(normalized):
        return OperatorIntent(
            kind="next_safe_test",
            command="operator-next-safe-test",
            reason="next safe test wording",
        )
    if _matches_project_health(normalized):
        return OperatorIntent(
            kind="project_health",
            command=":operator-check",
            reason="project health/status wording",
        )
    if _matches_smart_memory_status(normalized):
        return OperatorIntent(
            kind="smart_memory_status",
            command=":smart-memory",
            reason="memory status wording",
        )
    if _matches_urgent_status(normalized):
        return OperatorIntent(
            kind="urgent_status",
            command=":urgent-status",
            reason="urgent attention wording",
        )
    if _matches_best_next_action(normalized):
        return OperatorIntent(
            kind="best_next_action",
            command=":best-next-action",
            reason="single most important action wording",
        )
    if _matches_next_actions(normalized):
        return OperatorIntent(
            kind="next_actions",
            command=":next-actions",
            reason="next-step wording",
        )
    if _matches_autonomy_readiness(normalized):
        return OperatorIntent(
            kind="autonomy_readiness",
            command=":autonomy-readiness",
            reason="autonomy readiness wording",
        )
    if _matches_model_status(normalized):
        return OperatorIntent(
            kind="model_status",
            command=":models",
            reason="model routing/status wording",
        )
    if _matches_budget_status(normalized):
        return OperatorIntent(
            kind="budget_status",
            command=":operator-budget",
            reason="budget/token/spend wording",
        )
    if _matches_approval_status(normalized):
        return OperatorIntent(
            kind="approval_status",
            command=":approval-list all",
            reason="approval inbox wording",
        )
    return None


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())
