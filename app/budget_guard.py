"""Budget guard — turn an exhausted model budget into a resumable pause.

Wraps ``agent.run()`` so that a :class:`ModelBudgetExceeded` raised mid-cycle
does not just surface as an error string: the run's state is persisted as a
``paused`` checkpoint and queued as a paused task, so ``--resume <trace_id>``
can pick it up later (see ``docs/refactor/CLI_BASELINE.md`` §1.5).

Extracted verbatim from ``main.py`` as part of the incremental CLI decomposition.
It lives under ``app/`` rather than ``cli/`` because it is about *running the
agent*, not about the command-line surface — the CLI merely calls it. ``main.py``
re-exports every name below, so existing imports
(``from main import _run_agent_with_budget_guard``, …) keep working unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.task_scheduler_cli import _task_queue_for
from core.model_usage import ModelBudgetExceeded

if TYPE_CHECKING:  # heavy import, only needed for annotations
    from core.loop import AgentLoop


def _run_agent_with_budget_guard(
    agent: AgentLoop,
    *,
    user_question: str,
    file_hint: str | None = None,
    workspace: Path | None = None,
    stream: bool = True,
    deep_escalation=None,
) -> str:
    """Run the agent, optionally streaming synthesis tokens to stdout.

    When *stream* is True (default), synthesis tokens are printed to stdout
    as they arrive so the user sees a progressive response.  The full answer
    is still returned for post-processing (memory writes, formatting, etc.).
    """
    if stream:
        # Print a blank line before streaming starts so the answer is visually
        # separated from the spinner / log output on stderr.
        print("\n", end="", flush=True)
        _streaming_done = []

        def _on_token(text: str) -> None:
            print(text, end="", flush=True)
            _streaming_done.append(text)

        try:
            answer = agent.run(
                user_question=user_question,
                file_hint=file_hint,
                on_token=_on_token,
                deep_escalation=deep_escalation,
            )
        except ModelBudgetExceeded as exc:
            answer = f"Model budget exceeded: {exc}"
            agent.log.log("model_budget_blocked", {"error": str(exc)})
            _persist_resumable_budget_stop(
                agent,
                workspace=workspace,
                user_question=user_question,
                file_hint=file_hint,
                blocked=exc,
            )
        # End the streaming line cleanly; the caller will print the formatted
        # version below (which strips Output Contract headers / citations).
        if _streaming_done:
            print()  # newline after streamed tokens
        return answer
    try:
        return agent.run(user_question=user_question, file_hint=file_hint, deep_escalation=deep_escalation)
    except ModelBudgetExceeded as exc:
        message = f"Model budget exceeded: {exc}"
        agent.log.log("model_budget_blocked", {"error": str(exc)})
        _persist_resumable_budget_stop(
            agent,
            workspace=workspace,
            user_question=user_question,
            file_hint=file_hint,
            blocked=exc,
        )
        return message


def _workspace_from_agent(agent: AgentLoop, workspace: Path | None) -> Path | None:
    if workspace is not None:
        return workspace
    log_dir = getattr(getattr(agent, "log", None), "log_dir", None)
    if log_dir is None:
        return None
    try:
        return Path(log_dir).resolve().parent
    except Exception:
        return None


def _budget_block_payload(
    *,
    agent: AgentLoop,
    user_question: str,
    file_hint: str | None,
    blocked: ModelBudgetExceeded,
) -> dict:
    trace_id = getattr(getattr(agent, "log", None), "trace_id", "")
    return {
        "active_goal": f"Answer the question: {user_question}",
        "goal_id": "",
        "original_user_question": user_question,
        "file_hint": file_hint,
        "current_phase": "budget_guard",
        "planned_steps": [],
        "completed_steps": [],
        "remaining_steps": [],
        "stop_reason": "budget_exhausted",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "blocked_model": blocked.to_dict(),
        "trace_id": trace_id,
    }


def _existing_paused_checkpoint(agent: AgentLoop) -> dict | None:
    log = getattr(agent, "log", None)
    trace_id = getattr(log, "trace_id", None)
    log_dir = getattr(log, "log_dir", None)
    if not trace_id or log_dir is None:
        return None
    try:
        from core.checkpoint import CheckpointLoader, PHASE_PAUSED

        ctx = CheckpointLoader(Path(log_dir)).load(trace_id)
        if ctx is not None and ctx.last_phase == PHASE_PAUSED and ctx.paused:
            payload = dict(ctx.paused)
            payload.setdefault("trace_id", trace_id)
            return payload
    except Exception:
        return None
    return None


def _persist_resumable_budget_stop(
    agent: AgentLoop,
    *,
    workspace: Path | None,
    user_question: str,
    file_hint: str | None,
    blocked: ModelBudgetExceeded,
) -> None:
    log = getattr(agent, "log", None)
    trace_id = getattr(log, "trace_id", "")
    payload = _existing_paused_checkpoint(agent) or _budget_block_payload(
        agent=agent,
        user_question=user_question,
        file_hint=file_hint,
        blocked=blocked,
    )
    if not payload.get("trace_id"):
        payload["trace_id"] = trace_id

    if payload.get("current_phase") == "budget_guard":
        try:
            from core.checkpoint import CheckpointWriter

            CheckpointWriter(trace_id=trace_id, log_dir=log.log_dir).save_paused(payload)
            agent.log.log(
                "resumable_checkpoint_paused",
                {
                    "current_phase": payload["current_phase"],
                    "stop_reason": payload["stop_reason"],
                    "planned_steps": 0,
                    "completed_steps": 0,
                    "remaining_steps": 0,
                    "blocked_model": payload["blocked_model"],
                },
            )
        except Exception:
            pass

    resolved_workspace = _workspace_from_agent(agent, workspace)
    if resolved_workspace is None:
        return
    try:
        task = _task_queue_for(agent, resolved_workspace).add_paused_checkpoint(
            goal=str(payload.get("active_goal") or user_question),
            report=payload,
        )
        agent.log.log(
            "resumable_task_paused",
            {
                "task_id": task.id,
                "trace_id": payload.get("trace_id"),
                "stop_reason": payload.get("stop_reason"),
            },
        )
    except Exception:
        pass
