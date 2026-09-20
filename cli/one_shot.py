"""``--ask <question>``: the one-shot, memory-free run.

One question in, one answer out, then exit. The contract frozen by
``tests/characterization/test_cli_one_shot_policy.py``:

* the agent is built with ``with_memory=False`` **and**
  ``with_persistent=False`` -- a one-shot run neither reads nor writes
  ``data/persistent_memory.jsonl``;
* the approval policy follows ``--auto-approve``, where the default ``off``
  means *no provider wired at all*, so escalated tools stay blocked;
* an explicit ``:command`` is dispatched before any fuzzy intent routing, and
  the agent is built **before** dispatch even for a local, no-LLM command
  (frozen for extraction, not endorsed);
* deep/Opus escalation is opt-in: without ``--reason`` / ``--expect`` the
  escalation object stays ``None`` and a deep request downgrades;
* ``stream=False``, because the ``format_human_response`` print below is the
  sole output.

Every exit here is ``0``, including the unknown-command one -- the exit ``2``
cases (bad file hint, bad ``--resume``) are decided by the caller before this
runs.

**How the collaborators are reached, and why it matters for tests.** A
``monkeypatch.setattr`` is observed only where the *call site* resolves the
name, so the collaborator modules are imported as **modules** and called through
the attribute -- ``command_dispatch.handle_meta_command(...)``. One patch on the
module that defines the function is then seen from here and from ``cli/repl.py``
alike; binding the names at import time would silently ignore it.

``build_agent`` is the exception, passed in as a parameter: it is startup wiring
and belongs to ``cli/app.py``, which owns the startup sequence and is where the
suites patch it.
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
    # Set when this one-shot resumes a budget pause: the paused trace_id,
    # passed through to the guard so a completed resume retires its task.
    resumed_from: str | None = None,
    # Wiring seam: cli/app.py passes its own binding, so a patch there is
    # observed here too. Default keeps this function runnable on its own.
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
    #
    # with_experience=True, and it is a DIFFERENT axis — слово оператора
    # 2026-09-20. Опыт (эпизоды и процедуры) отключался здесь не по решению,
    # а по умолчанию `with_experience = with_memory` в bootstrap. Тот же самый
    # изъян уже чинили однажды для безлюдного пути — комментарий рядом с
    # `if with_experience:` говорит, что связка «оставляла агента неспособным
    # записывать и вспоминать опыт вообще», — и канал разговора тогда забыли.
    #
    # Цена измерена в живом разговоре 2026-09-20: за вечер через `--ask`
    # прошло около двадцати пяти обменов, в которых агент установил, что `-I`
    # отбрасывает PYTHONPATH, что его собственная заявка предлагает нерабочее
    # лекарство, что он объясняет стену вместо проверки. В эпизодической
    # памяти после этого — НОЛЬ записей об этом разговоре (замер: из 200
    # эпизодов ни один не несёт вопроса оператора). Складывать было некуда:
    # `episodic_store` равен None, и каждая запись падала в пустоту.
    #
    # Обещание «no memory, fresh session» этим не нарушено: оно про сессию и
    # про `data/persistent_memory.jsonl`, и обе оси остаются выключенными.
    # Замороженный контракт (`tests/characterization/test_cli_one_shot_policy.py`)
    # проверяет ровно эти две и об опыте не говорит ничего.
    #
    # episodic_replay=False нарочно: память должна ПОДСКАЗЫВАТЬ, а не
    # подменять работу готовым ответом из прошлого. Разговор ведут ради
    # нового измерения, а не ради пересказа старого.
    agent = build_agent(
        workspace,
        with_memory=False,
        with_persistent=False,
        with_experience=True,
        episodic_replay=False,
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
        resumed_from=resumed_from,
    )
    print("\n" + format_human_response(answer) + "\n")
    return 0
