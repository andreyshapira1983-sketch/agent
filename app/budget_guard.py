"""Run the agent so that an exhausted model budget becomes a resumable pause."""
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
    """Run the agent, optionally streaming synthesis tokens to stdout; return the answer.

    resumed_from: the paused trace this run resumes; its paused task is retired
    only when the run completes without a new budget stop.
    """
    if stream:
        # Separates the streamed answer from spinner/log output on stderr.
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
        if _streaming_done:
            print()  # newline after streamed tokens
        return answer
    try:
        answer = agent.run(user_question=user_question, file_hint=file_hint, deep_escalation=deep_escalation)
        _retire_resumed_pause(agent, workspace=workspace, resumed_from=resumed_from)
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
    else:
        return answer


def _retire_resumed_pause(
    agent: AgentLoop,
    *,
    workspace: Path | None,
    resumed_from: str | None,
) -> None:
    """Mark done the paused task that a completed resume run has made obsolete.

    Matched by ``resumed_from`` because the resumed run carries a fresh trace_id.
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
    except Exception:  # noqa: BLE001, S110 — advisory scan; no match changes nothing
        pass


def _workspace_from_agent(agent: AgentLoop, workspace: Path | None) -> Path | None:
    if workspace is not None:
        return workspace
    log_dir = getattr(getattr(agent, "log", None), "log_dir", None)
    if log_dir is None:
        return None
    try:
        return Path(log_dir).resolve().parent
    except (OSError, TypeError, ValueError):
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
    except (ImportError, OSError):
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
        except Exception:  # noqa: BLE001, S110 — checkpoint is best-effort; the run still pauses
            pass

    resolved_workspace = _workspace_from_agent(agent, workspace)
    if resolved_workspace is None:
        return
    # A live conversation turn is not queued: the operator retypes it, and the
    # queue feeds unattended mode. The checkpoint above still allows --resume.
    gateway_path = getattr(agent, "gateway_path", INTERACTIVE_GATEWAY_PATH)
    if not checkpoint_is_resumable_work(gateway_path):
        agent.log.log("resumable_task_not_queued", {
            "gateway_path": str(gateway_path),
            "reason": "interrupted turn was a live conversation, not unattended work",
            "stop_reason": payload.get("stop_reason"),
        })
        # Without a queue entry `:task-list` shows no resume hint, so print it here.
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
    except Exception:  # noqa: BLE001, S110 — queue entry is best-effort; the run still pauses
        pass
