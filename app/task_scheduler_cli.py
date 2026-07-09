"""Runtime task queue and scheduler REPL handlers.

Split out of ``main.py``. Owns queue/scheduler store accessors and the
``:queue-status`` / ``:task-*`` / ``:schedule-*`` command surface. Does not own
operator-task reports, dispatch, or approval decision handlers.

``main.py`` re-exports handlers and ``_task_queue_for`` / ``_scheduler_for`` for
other modules that still attach to the agent-scoped stores.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.bootstrap import DEFAULT_RUNTIME_SCHEDULES_PATH, DEFAULT_RUNTIME_TASKS_PATH
from cli.commands_approval import _approval_inbox_for
from cli.parsers import _split_meta_args
from core.autonomous_runtime import AutonomousRuntime
from core.loop import AgentLoop
from core.scheduler import SchedulerStore
from core.task_queue import TaskQueueStore


def _task_queue_for(agent: AgentLoop, workspace: Path) -> TaskQueueStore:
    queue = getattr(agent, "runtime_task_queue", None)
    if queue is None:
        queue = TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH)
        setattr(agent, "runtime_task_queue", queue)
    return queue


def _scheduler_for(agent: AgentLoop, workspace: Path) -> SchedulerStore:
    scheduler = getattr(agent, "runtime_scheduler", None)
    if scheduler is None:
        scheduler = SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH)
        setattr(agent, "runtime_scheduler", scheduler)
    return scheduler


def _parse_runtime_task_options(rest: str) -> tuple[dict, str | None]:
    tokens = _split_meta_args(rest)
    dry_run = True
    include_tests = True
    limit = 5
    learning_limit = 5
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
            i += 1
            continue
        if token == "--tests":
            include_tests = True
            i += 1
            continue
        if token == "--no-tests":
            include_tests = False
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                return {}, "Usage: --limit requires a number"
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                return {}, "Usage: --limit requires a number"
            i += 2
            continue
        if token == "--learning-limit":
            if i + 1 >= len(tokens):
                return {}, "Usage: --learning-limit requires a number"
            try:
                learning_limit = int(tokens[i + 1])
            except ValueError:
                return {}, "Usage: --learning-limit requires a number"
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    if limit < 1 or learning_limit < 1:
        return {}, "Usage: limits must be >= 1"

    return {
        "goal": " ".join(goal_parts).strip() or "project health",
        "dry_run": dry_run,
        "include_tests": include_tests,
        "limit": limit,
        "learning_limit": learning_limit,
    }, None


def _handle_queue_status(agent: AgentLoop, workspace: Path) -> bool:
    summary = _task_queue_for(agent, workspace).summary()
    print("=== runtime task queue ===", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _handle_scheduler_status(agent: AgentLoop, workspace: Path) -> bool:
    summary = _scheduler_for(agent, workspace).summary()
    print("=== runtime scheduler ===", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _handle_task_add(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    opts, error = _parse_runtime_task_options(rest)
    if error:
        print(error, file=sys.stderr)
        return True
    task = _task_queue_for(agent, workspace).add(**opts)
    agent.log.log("runtime_task_added", task.to_dict())
    print(
        f"(task added: {task.id}; goal={task.goal}; dry_run={task.dry_run}; "
        f"tests={task.include_tests})",
        file=sys.stderr,
    )
    return True


def _handle_task_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    status = rest.strip().lower() or "all"
    tasks = _task_queue_for(agent, workspace).list(status=status)
    if not tasks:
        print(f"(no tasks: status={status})", file=sys.stderr)
        return True
    print(f"=== runtime tasks ({len(tasks)}; status={status}) ===", file=sys.stderr)
    for task in tasks:
        report = task.last_report or {}
        resume_hint = ""
        if task.kind == "resume_checkpoint":
            trace_id = report.get("trace_id") or "-"
            stop_reason = report.get("stop_reason") or task.last_error or "-"
            resume_hint = f" stop_reason={stop_reason} resume={trace_id}"
        print(
            f"  {task.id} [{task.status}] priority={task.priority} "
            f"attempts={task.attempts}/{task.max_attempts} run_after={task.run_after} "
            f"goal={task.goal}{resume_hint}",
            file=sys.stderr,
        )
    return True


def _handle_task_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    limit = 1
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"Usage: unknown :task-run option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True
    report = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=_approval_inbox_for(agent, workspace),
    ).run_task_queue(_task_queue_for(agent, workspace), max_tasks=limit)
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_task_cancel(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    task_id = rest.strip()
    if not task_id:
        print("Usage: :task-cancel <task_id>", file=sys.stderr)
        return True
    try:
        task = _task_queue_for(agent, workspace).cancel(task_id)
    except KeyError as exc:
        print(f"(task cancel failed: {exc})", file=sys.stderr)
        return True
    print(f"(task cancelled: {task.id})", file=sys.stderr)
    return True


def _parse_schedule_add(rest: str) -> tuple[dict, str | None]:
    tokens = _split_meta_args(rest)
    every_minutes: int | None = None
    name: str | None = None
    task_opts = {
        "dry_run": True,
        "include_tests": True,
        "limit": 5,
        "learning_limit": 5,
    }
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--name":
            if i + 1 >= len(tokens):
                return {}, "Usage: --name requires a value"
            name = tokens[i + 1]
            i += 2
            continue
        if token == "--every-minutes":
            if i + 1 >= len(tokens):
                return {}, "Usage: --every-minutes requires a number"
            try:
                every_minutes = int(tokens[i + 1])
            except ValueError:
                return {}, "Usage: --every-minutes requires a number"
            i += 2
            continue
        if token == "--dry-run":
            task_opts["dry_run"] = True
            i += 1
            continue
        if token == "--allow-effects":
            task_opts["dry_run"] = False
            i += 1
            continue
        if token == "--tests":
            task_opts["include_tests"] = True
            i += 1
            continue
        if token == "--no-tests":
            task_opts["include_tests"] = False
            i += 1
            continue
        if token in {"--limit", "--learning-limit"}:
            if i + 1 >= len(tokens):
                return {}, f"Usage: {token} requires a number"
            try:
                value = int(tokens[i + 1])
            except ValueError:
                return {}, f"Usage: {token} requires a number"
            key = "limit" if token == "--limit" else "learning_limit"
            task_opts[key] = value
            i += 2
            continue
        if every_minutes is None and token.isdigit():
            every_minutes = int(token)
            i += 1
            continue
        goal_parts.append(token)
        i += 1

    if every_minutes is None:
        return {}, "Usage: :schedule-add <minutes> <goal> [--name NAME]"
    if every_minutes < 1:
        return {}, "Usage: minutes must be >= 1"
    if task_opts["limit"] < 1 or task_opts["learning_limit"] < 1:
        return {}, "Usage: limits must be >= 1"
    goal = " ".join(goal_parts).strip() or "project health"
    return {
        "name": name or f"every-{every_minutes}-min",
        "goal": goal,
        "every_minutes": every_minutes,
        **task_opts,
    }, None


def _handle_schedule_add(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    opts, error = _parse_schedule_add(rest)
    if error:
        print(error, file=sys.stderr)
        return True
    schedule = _scheduler_for(agent, workspace).add(**opts)
    agent.log.log("runtime_schedule_added", schedule.to_dict())
    print(
        f"(schedule added: {schedule.id}; every={schedule.every_minutes}m; "
        f"next={schedule.next_run_at}; goal={schedule.goal})",
        file=sys.stderr,
    )
    return True


def _handle_schedule_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    status = rest.strip().lower() or "all"
    schedules = _scheduler_for(agent, workspace).list(status=status)
    if not schedules:
        print(f"(no schedules: status={status})", file=sys.stderr)
        return True
    print(f"=== runtime schedules ({len(schedules)}; status={status}) ===", file=sys.stderr)
    for schedule in schedules:
        print(
            f"  {schedule.id} [{schedule.status}] every={schedule.every_minutes}m "
            f"next={schedule.next_run_at} name={schedule.name} goal={schedule.goal}",
            file=sys.stderr,
        )
    return True


def _schedule_disable_message(rest: str, workspace: Path) -> str:
    schedule_id = rest.strip()
    if not schedule_id:
        return "Usage: :schedule-disable <schedule_id>"
    try:
        schedule = SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH).disable(schedule_id)
    except KeyError:
        return "SCHEDULE_NOT_FOUND"
    return f"SCHEDULE_DISABLED {schedule.id}"


def _handle_schedule_disable(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    print(_schedule_disable_message(rest, workspace), file=sys.stderr)
    return True


def _handle_schedule_tick(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    run_after_tick = False
    limit: int | None = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--run":
            run_after_tick = True
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"Usage: unknown :schedule-tick option {token}", file=sys.stderr)
        return True
    if limit is not None and limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True
    tick = _scheduler_for(agent, workspace).tick(
        task_queue=_task_queue_for(agent, workspace),
        limit=limit,
    )
    agent.log.log("runtime_schedule_tick", tick.to_dict())
    print(tick.user_summary(), file=sys.stderr)
    if run_after_tick:
        report = AutonomousRuntime(
            agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        ).run_task_queue(
            _task_queue_for(agent, workspace),
            max_tasks=limit or 1,
            task_ids=tick.task_ids,
        )
        print(report.user_summary(), file=sys.stderr)
    return True
