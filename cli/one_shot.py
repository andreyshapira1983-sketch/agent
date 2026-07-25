"""``--ask <question>``: the one-shot, memory-free run.

One question in, one answer out, then exit. The contract recorded in
``docs/refactor/CLI_BASELINE.md`` section 2.4 and frozen by
``tests/characterization/test_cli_one_shot_policy.py``:

* the agent is built with ``with_memory=False`` **and**
  ``with_persistent=False`` -- a one-shot run neither reads nor writes
  ``data/persistent_memory.jsonl``;
* the approval policy follows ``--auto-approve``, where the default ``off``
  means *no provider wired at all*, so escalated tools stay blocked;
* an explicit ``:command`` is dispatched before any fuzzy intent routing, and
  the agent is built **before** dispatch even for a local, no-LLM command
  (frozen for extraction, not endorsed -- see CLI_BASELINE section 3);
* deep/Opus escalation is opt-in: without ``--reason`` / ``--expect`` the
  escalation object stays ``None`` and a deep request downgrades;
* ``stream=False``, because the ``format_human_response`` print below is the
  sole output.

Every exit here is ``0``, including the unknown-command one -- the exit ``2``
cases (bad file hint, bad ``--resume``) are decided by the caller before this
runs.

Extracted from ``main()``. Like ``cli/resume.py`` this could not be a verbatim
move, because the code lived *inside* ``main()``. The body is the original block
with the ``args.*`` reads replaced by parameters (``args.ask`` -> ``ask``,
``args.file`` -> ``file_hint``, ``args.auto_approve`` -> ``auto_approve``,
``args.reason`` -> ``reason``, ``args.expect`` -> ``expect``), plus two
non-behavioural fixes: the ``approval_provider`` annotation now admits ``None``
(the ``off`` branch always assigned it), and a comment that pointed at
"the docstring at line 9" now names ``main.py``'s module docstring instead of a
line number that drifts. Every message string and every call is untouched.

**How the collaborators are reached, and why it matters for tests.** A
``monkeypatch.setattr`` is observed only where the *call site* resolves the name
(``docs/refactor/CLI_BASELINE.md`` section 2.5). The three collaborator modules
below are therefore imported as **modules** and called through the attribute --
``command_dispatch.handle_meta_command(...)`` -- so one patch on the module that
*defines* the function is seen from here and from ``cli/repl.py`` alike. Binding
the names at import time instead would silently ignore such a patch.

``build_agent`` is the exception still passed in by ``main()``: it is wiring, it
is patched on ``main`` in 22 places, and it moves in the next Phase 7 step
together with the rest of the startup sequence.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from app import budget_guard
from app.bootstrap import build_agent as _build_agent
from cli import command_dispatch, intent_bridge
from core.approval import ApprovalProvider, AutoApprover
from core.loop import format_human_response

if TYPE_CHECKING:  # annotations only
    from collections.abc import Callable
    from pathlib import Path


def run_one_shot(
    ask: str,
    *,
    workspace: Path,
    file_hint: str | None = None,
    auto_approve: str = "off",
    reason: str | None = None,
    expect: str | None = None,
    # Wiring seam — main() passes its own binding so patches on `main` stay
    # observable until the startup sequence moves (Phase 7, next step).
    build_agent: Callable[..., object] = _build_agent,
) -> int:
    """Run a single question end-to-end and return the process exit code."""
    # Approval provider selection. One-shot can't realistically prompt a
    # human, so it falls back to AutoApprover unless the user opted in via
    # --auto-approve. Interactive uses the live CLI prompt by default.
    if auto_approve == "approve":
        approval_provider: ApprovalProvider | None = AutoApprover(default="approve")
    elif auto_approve == "deny":
        approval_provider = AutoApprover(default="deny")
    else:
        # 'off' in one-shot = no provider wired = escalated tools blocked.
        approval_provider = None

    # with_persistent=False: one-shot must NOT read or mutate
    # data/persistent_memory.jsonl — main.py's module docstring promises
    # "no memory, fresh session", so persistent memory must be excluded
    # too, not just working (session) memory.
    agent = build_agent(
        workspace,
        with_memory=False,
        with_persistent=False,
        approval_provider=approval_provider,
    )
    # Explicit ':' meta-commands take precedence over fuzzy intent routing,
    # mirroring the interactive REPL — otherwise e.g. ':campaign-start
    # --max-cost-units 0' is misread as a budget query by the classifier.
    ask_head = ask.lstrip()
    if ask_head.startswith(":") or ask_head == "?":
        if command_dispatch.handle_meta_command(ask_head, agent, workspace):
            return 0
        print(f"(unknown command: {ask_head})", file=sys.stderr)
        return 0
    if intent_bridge._handle_local_operator_reply(ask, agent):
        return 0
    if intent_bridge.handle_conversational_operator_input(ask, agent, workspace):
        return 0
    # Deep/Opus escalation is opt-in and operator-driven: only an explicit
    # --reason (with --expect) lets planner/synthesizer reach the deep tier.
    # Without it, deep_escalation stays None and every deep request
    # downgrades to the standard model.
    deep_escalation = None
    if reason or expect:
        from core.deep_escalation import OperatorEscalation
        deep_escalation = OperatorEscalation(
            reason=reason,
            expected_output=expect,
        )
    # stream=False: the formatted print below is the sole output.
    # With stream=True the raw Output-Contract tokens arrive first, then
    # format_human_response reprints the same content — double output.
    answer = budget_guard._run_agent_with_budget_guard(
        agent,
        user_question=ask,
        file_hint=file_hint,
        workspace=workspace,
        stream=False,
        deep_escalation=deep_escalation,
    )
    print("\n" + format_human_response(answer) + "\n")
    return 0
