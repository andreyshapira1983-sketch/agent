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

**Why the five collaborators are parameters.** ``main`` is a load-bearing
import *and monkeypatch* surface (``docs/refactor/CLI_BASELINE.md`` section 2.5):
suites do ``monkeypatch.setattr(main, "build_agent", ...)``, and such a patch is
only observed where the *call site* resolves the name in ``main``'s namespace.
Moving these call sites out of ``main.py`` would have turned 25 characterization
tests red and -- worse -- let 2 of them keep passing while quietly building a
real agent and dispatching a real command. So ``main()`` passes its own
module-level bindings in, and this module's imports are only the defaults for
direct callers. The parameters can be dropped in Phase 7, together with the
``main.py`` re-export block they exist to preserve.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from app.bootstrap import build_agent as _build_agent
from app.budget_guard import _run_agent_with_budget_guard
from cli.command_dispatch import handle_meta_command as _handle_meta_command
from cli.intent_bridge import (
    _handle_local_operator_reply,
    handle_conversational_operator_input as _handle_conversational_operator_input,
)
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
    # Patch seam — see the module docstring. Defaults are the real functions;
    # main() passes its own bindings so patches on `main` stay observable.
    build_agent: Callable[..., object] = _build_agent,
    handle_meta_command: Callable[..., bool] = _handle_meta_command,
    handle_local_operator_reply: Callable[..., bool] = _handle_local_operator_reply,
    handle_conversational: Callable[..., bool] = _handle_conversational_operator_input,
    run_agent_with_budget_guard: Callable[..., str] = _run_agent_with_budget_guard,
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
        if handle_meta_command(ask_head, agent, workspace):
            return 0
        print(f"(unknown command: {ask_head})", file=sys.stderr)
        return 0
    if handle_local_operator_reply(ask, agent):
        return 0
    if handle_conversational(ask, agent, workspace):
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
    answer = run_agent_with_budget_guard(
        agent,
        user_question=ask,
        file_hint=file_hint,
        workspace=workspace,
        stream=False,
        deep_escalation=deep_escalation,
    )
    print("\n" + format_human_response(answer) + "\n")
    return 0
