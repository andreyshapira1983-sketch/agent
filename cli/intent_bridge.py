"""The bridge from plain operator language to an explicit command.

An operator does not have to type ``:commands``. A message is first run through a
**deterministic, no-LLM** matcher (``core.operator_intent``); on a positive match
for one of the softer status/capability intents the model is asked whether the
message is really a request, and it may only **cancel** the routing -- kernel
decides, model advises. Anything unmatched falls through to the normal agent loop
untouched.

Extracted verbatim from ``main.py``. Import these names from here: the
``main.py`` re-exports that used to mirror them were removed in Phase 7, and a
fake for any of them belongs on this module, where the call sites resolve it.

Note for anyone stubbing these in a test: the functions resolve each other in
**this** module's namespace, so a stand-in has to replace the name here, not on
``main``.

``_local_operator_reply`` is the narrow no-LLM path: an explicit "reply only
with: ..." instruction that also forbids planner/synthesizer is answered locally
and never reaches a model.
"""
from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from app.operator_status import (
    _handle_autonomy_readiness,
    _handle_next_actions,
    _handle_next_safe_test,
    _handle_operator_budget,
    _handle_operator_capability_check,
    _handle_operator_check,
    _handle_operator_gaps_check,
    _handle_operator_weakness_finder,
    _handle_programming_readiness,
    _handle_urgent_status,
)
from cli.commands_approval import (
    _handle_approval_list,
    _handle_best_next_action,
)
from cli.commands_ingest import (
    _handle_implementation_plan,
    _handle_patch_proposal_plan,
    _handle_source_review_plan,
)
from cli.commands_memory import _handle_smart_memory
from cli.commands_misc import _handle_architecture_audit
from cli.commands_models import _handle_models
from cli.commands_proposals import (
    _handle_capability_request,
    _handle_subagent_proposal,
)
from cli.commands_self_build import _handle_self_build_produce
from cli.commands_self_task import _handle_self_task_propose
from core.intent_understanding import understand_intent
from core.loop import format_human_response
from core.model_router import ModelRole
from core.operator_intent import route_operator_intent
from core.strategy_router import classify_operator_strategy

if TYPE_CHECKING:  # annotations only
    from pathlib import Path

    from core.loop import AgentLoop
    from core.operator_intent import OperatorIntent

_REPLY_ONLY_STOP_RE = re.compile(
    r"\breply\s+only\s+with:\s*(?:\"([^\"]+)\"|'([^']+)'|“([^”]+)”)",
    re.IGNORECASE | re.DOTALL,
)


def _local_operator_reply(text: str, agent: AgentLoop | None = None) -> str | None:
    """Return a local response for explicit stop/ack operator instructions.

    TD-001: some operator-control messages are intentionally local and must not
    enter Planner/Synthesizer. Keep this narrow: only honour a quoted
    "Reply only with:" directive when the same instruction explicitly forbids
    the expensive model path.
    """
    normalized = " ".join((text or "").casefold().split())
    if "reply only with:" not in normalized:
        return None
    llm_stop_markers = (
        "do not call planner",
        "do not call planner or synthesizer",
        "do not use claude",
        "do not make a plan with llm",
        "не вызывай planner",
        "не вызывай synthesizer",
        "не используй claude",
        "не использовать claude",
    )
    if not any(marker in normalized for marker in llm_stop_markers):
        return None
    match = _REPLY_ONLY_STOP_RE.search(text)
    if match is None:
        return None
    answer = next((part for part in match.groups() if part), "").strip()
    if not answer:
        return None
    if agent is not None:
        agent.log.log(
            "local_operator_reply",
            {"reason": "reply_only_stop_instruction", "answer_preview": answer[:120]},
        )
    return answer


def _handle_local_operator_reply(text: str, agent: AgentLoop) -> bool:
    answer = _local_operator_reply(text, agent)
    if answer is None:
        return False
    print("\n" + format_human_response(answer) + "\n")
    return True


# "Soft" status/capability intents that conversational phrasing can trip. For
# these, the keyword match is VERIFIED by the model (it tells a request apart
# from a passing mention) before dispatch. Explicit imperative intents are not
# gated.
_VERIFY_INTENTS: frozenset[str] = frozenset({
    "capability_check", "project_health", "smart_memory_status",
    "current_gaps_check", "weakness_finder", "next_safe_test",
    "best_next_action", "next_actions", "autonomy_readiness",
    "model_status", "budget_status", "approval_status", "urgent_status",
})


def _model_says_conversation(text: str, intent: OperatorIntent, agent: AgentLoop) -> bool:
    """True only when the model gives a CLEAR, parseable "this is conversation"
    verdict for a soft keyword match.

    On a confirmed action, or any uncertainty (model error / unparseable /
    ungrounded / low confidence / no model), returns False — so deterministic
    routing is preserved and the model only ever *vetoes* an obvious
    conversational false-positive (model advises, kernel decides).
    """
    try:
        llm = agent.model_router.for_role(ModelRole.PLANNER)
    except Exception:  # noqa: BLE001 — no model to consult -> keep routing
        return False
    decision = understand_intent(text, available_actions=(intent.kind,), llm=llm)
    return decision.kind == "conversation" and decision.source == "model"


def handle_conversational_operator_input(text: str, agent: AgentLoop, workspace: Path) -> bool:
    strategy = classify_operator_strategy(text)
    agent.log.log(
        "strategy_classified",
        {"strategy": strategy.value, "text_preview": text[:120]},
    )
    intent = route_operator_intent(text)
    if intent is None:
        return False
    # Bridge: for a soft match, let the model VETO an obvious conversational
    # false-positive. It only suppresses on a clear "this is conversation"
    # verdict; on uncertainty the deterministic route is preserved.
    if intent.kind in _VERIFY_INTENTS and _model_says_conversation(text, intent, agent):
        agent.log.log(
            "operator_intent_suppressed",
            {"kind": intent.kind, "reason": "model judged conversation, not a request"},
        )
        return False
    agent.log.log("operator_intent", intent.to_dict())
    if intent.kind == "shell_command_hint":
        print(
            "This looks like a shell/PowerShell command. "
            "Run it in PowerShell, not inside the agent REPL.",
            file=sys.stderr,
        )
        return True
    print(
        f"(operator intent: {intent.kind}; internal={intent.command})",
        file=sys.stderr,
    )
    return _dispatch_operator_intent(intent, agent, workspace, original_text=text)


def _dispatch_operator_intent(
    intent: OperatorIntent,
    agent: AgentLoop,
    workspace: Path,
    *,
    original_text: str = "",
) -> bool:
    if intent.kind == "capability_request":
        return _handle_capability_request(original_text, agent, workspace)
    if intent.kind == "self_build_request":
        return _handle_self_build_produce("", agent, workspace)
    if intent.kind == "subagent_proposal":
        return _handle_subagent_proposal(original_text.strip(), agent, workspace)
    if intent.kind == "architecture_audit":
        return _handle_architecture_audit("", agent, workspace)
    if intent.kind == "self_task_proposal":
        return _handle_self_task_propose("", agent, workspace)
    if intent.kind == "safe_self_check":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "capability_check":
        return _handle_operator_capability_check(agent, workspace)
    if intent.kind == "programming_readiness":
        return _handle_programming_readiness("", agent, workspace)
    if intent.kind == "current_gaps_check":
        return _handle_operator_gaps_check(agent, workspace)
    if intent.kind == "weakness_finder":
        return _handle_operator_weakness_finder(agent, workspace)
    if intent.kind == "next_safe_test":
        return _handle_next_safe_test(agent, workspace)
    if intent.kind == "project_health":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "smart_memory_status":
        return _handle_smart_memory("", agent)
    if intent.kind == "model_status":
        return _handle_models("", agent)
    if intent.kind == "budget_status":
        return _handle_operator_budget("", agent, workspace)
    if intent.kind == "approval_status":
        return _handle_approval_list("all", agent, workspace)
    if intent.kind == "urgent_status":
        return _handle_urgent_status("", agent, workspace)
    if intent.kind == "best_next_action":
        return _handle_best_next_action("", agent, workspace)
    if intent.kind == "next_actions":
        return _handle_next_actions("", agent, workspace)
    if intent.kind == "autonomy_readiness":
        return _handle_autonomy_readiness("", agent, workspace)
    if intent.kind == "source_review_plan":
        return _handle_source_review_plan(original_text, agent, workspace)
    if intent.kind == "implementation_plan":
        return _handle_implementation_plan(original_text, agent, workspace)
    if intent.kind == "patch_proposal":
        return _handle_patch_proposal_plan(original_text, agent, workspace)
    return False
