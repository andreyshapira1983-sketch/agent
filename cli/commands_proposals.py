"""``:capability-request`` and ``:subagent-proposal`` REPL commands.

Both answer the same shape of operator request -- "you are missing X, propose how
to get it" -- and both end in the same place: a proposal the human must approve.
Neither acts on its own.

* ``:capability-request <goal> [--submit] [--json]`` names the missing connector
  or capability boundary for a goal and, with ``--submit``, files it in the
  approval inbox.
* ``:subagent-proposal <goal> [--submit]`` drafts a bounded sub-agent contract for
  a goal that looks like it needs delegation, and with ``--submit`` files that.

Extracted verbatim from ``main.py`` as part of the incremental CLI decomposition;
``main.py`` re-exports both handlers so existing callers keep working.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.capability_request import propose_capability_request
from core.model_router import ModelRole
from core.subagent_contract import approval_payload_from_proposal
from core.subagent_memory_scope import needs_delegation, propose_subagent

from cli.commands_approval import _approval_inbox_for
from cli.parsers import _split_meta_args

if TYPE_CHECKING:  # heavy import, only needed for annotations
    from core.loop import AgentLoop


def _handle_capability_request(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :capability-request — propose missing capability boundaries."""
    tokens = _split_meta_args(rest)
    submit = False
    as_json = False
    goal_parts: list[str] = []

    for token in tokens:
        if token == "--submit":
            submit = True
        elif token == "--json":
            as_json = True
        else:
            goal_parts.append(token)

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :capability-request <goal> [--submit] [--json]", file=sys.stderr)
        return True

    try:
        request = propose_capability_request(goal)
    except ValueError as exc:
        print(f"(capability-request failed: {exc})", file=sys.stderr)
        return True

    agent.log.log("capability_request_proposed", request.to_dict())
    if as_json:
        print(json.dumps(request.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(request.user_summary(), file=sys.stderr)

    if submit:
        inbox = _approval_inbox_for(agent, workspace)
        item = inbox.add(
            operation="capability_request",
            summary=f"Capability request: {request.capability_type} — {goal}",
            risk="external",
            reasons=(request.why_needed, request.human_risk_summary),
            payload=request.to_dict(),
        )
        print(f"(capability-request submitted to approval inbox id={item.id})", file=sys.stderr)

    return True


def _handle_subagent_proposal(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :subagent-proposal command — generate an autonomous subagent proposal."""
    tokens = _split_meta_args(rest)
    submit = False
    goal_parts: list[str] = []

    for token in tokens:
        if token == "--submit":
            submit = True
        else:
            goal_parts.append(token)

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :subagent-proposal <goal> [--submit]", file=sys.stderr)
        return True

    hint = "(needs delegation)" if needs_delegation(goal) else "(may not need delegation)"
    print(f"(subagent-proposal goal={goal!r} {hint})", file=sys.stderr)

    try:
        result = propose_subagent(
            goal,
            llm=agent.model_router.for_task(ModelRole.PLANNER, goal),
            logger=agent.logger if hasattr(agent, "logger") else None,
        )
    except Exception as exc:
        print(f"(subagent-proposal failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)

    if result.ok and result.proposal is not None and submit:
        inbox = _approval_inbox_for(agent, workspace)
        item = inbox.add(
            operation="launch_subagent",
            summary=f"Subagent proposal: {result.proposal.proposed_role} — {goal}",
            risk="reversible",
            reasons=(f"why: {result.proposal.why_needed}",),
            payload=approval_payload_from_proposal(result.proposal),
        )
        print(f"(subagent-proposal submitted to approval inbox id={item.id})", file=sys.stderr)

    return True
