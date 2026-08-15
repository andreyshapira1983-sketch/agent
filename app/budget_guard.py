"""Budget guard — turn an exhausted model budget into a resumable pause.

Wraps ``agent.run()`` so that a :class:`ModelBudgetExceeded` raised mid-cycle
does not just surface as an error string: the run's state is persisted as a
``paused`` checkpoint and queued as a paused task, so ``--resume <trace_id>``
can pick it up later.

Extracted verbatim from ``main.py`` as part of the incremental CLI decomposition.
It lives under ``app/`` rather than ``cli/`` because it is about *running the
agent*, not about the command-line surface — the CLI merely calls it. Import the
helpers from here; the ``main.py`` re-exports that used to mirror them were
removed with the rest of the compatibility block in Phase 7.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.task_scheduler_cli import _task_queue_for
from core.model_usage import ModelBudgetExceeded
from core.task_queue import INTERACTIVE_GATEWAY_PATH, checkpoint_is_resumable_work

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
    resumed_from: str | None = None,
) -> str:
    """Run the agent, optionally streaming synthesis tokens to stdout.

    When *stream* is True (default), synthesis tokens are printed to stdout
    as they arrive so the user sees a progressive response.  The full answer
    is still returned for post-processing (memory writes, formatting, etc.).

    *resumed_from* names the paused trace this run resumes (from
    ``ResumeDecision.resumed_paused_trace``). When the run completes without
    a new budget stop, the paused task that trace queued is retired; a run
    that pauses again retires nothing — the work is still not done.
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
            _retire_resumed_pause(agent, workspace=workspace, resumed_from=resumed_from)
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
        answer = agent.run(user_question=user_question, file_hint=file_hint, deep_escalation=deep_escalation)
        _retire_resumed_pause(agent, workspace=workspace, resumed_from=resumed_from)
        return answer
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


def _retire_resumed_pause(
    agent: AgentLoop,
    *,
    workspace: Path | None,
    resumed_from: str | None,
) -> None:
    """Close the pause record a completed resume run has just made obsolete.

    The resumed run carries a fresh trace_id, so the join to the old paused
    task goes through ``resumed_from`` — the trace ``resolve_resume`` restored.
    Best-effort like the rest of this module: queue trouble must not eat the
    answer the user is owed.
    """
    if not resumed_from:
        return
    resolved_workspace = _workspace_from_agent(agent, workspace)
    if resolved_workspace is None:
        return
    try:
        queue = _task_queue_for(agent, resolved_workspace)
        for task in queue.list(status="paused"):
            if task.kind != "resume_checkpoint":
                continue
            if (task.last_report or {}).get("trace_id") != resumed_from:
                continue
            report = dict(task.last_report or {})
            report["resumed_by"] = getattr(getattr(agent, "log", None), "trace_id", "")
            queue.mark_done(task.id, report=report)
            agent.log.log(
                "resumable_task_retired",
                {
                    "task_id": task.id,
                    "trace_id": resumed_from,
                    "resumed_by": report["resumed_by"],
                },
            )
    except Exception:
        pass


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
        from core.checkpoint import PHASE_PAUSED, CheckpointLoader

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
    # Реплику в очередь работ не кладём: оператор сидит здесь и наберёт её
    # заново, а очередь кормит непригляданный режим. Контрольная точка выше
    # записана в любом случае — возобновить ход вручную по-прежнему можно.
    gateway_path = getattr(agent, "gateway_path", INTERACTIVE_GATEWAY_PATH)
    if not checkpoint_is_resumable_work(gateway_path):
        agent.log.log("resumable_task_not_queued", {
            "gateway_path": str(gateway_path),
            "reason": "interrupted turn was a live conversation, not unattended work",
            "stop_reason": payload.get("stop_reason"),
        })
        # Подсказку `resume=<trace>` печатал `:task-list` из очереди — то есть
        # ровно та строка, которую здесь больше не заводят. Без этой печати
        # оператор потерял бы единственное место, где узнавал, что ход можно
        # продолжить: точка сохранена, продолжать есть что.
        print(
            f"[resume] ход прерван ({payload.get('stop_reason')}); "
            f"продолжить: --resume {payload.get('trace_id') or trace_id}",
            file=sys.stderr,
        )
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
