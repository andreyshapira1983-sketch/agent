"""Команды оператора для команды агентов: план и запуск.

Приехало из `cli/commands_misc.py`, которого больше нет. Тот файл сам себя
называл «разное» и держал шесть несвязанных тем: по имени нельзя было узнать,
что внутри, а войти в него приходилось за любой из шести.

Здесь две: `:team-plan` строит план по задаче, `:team-run` исполняет его под
бюджетом. Разбор аргументов и печать — весь код; решения принимают
`core.team_plan` и `core.team_executor`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.team_executor import TeamBudget, TeamExecutor
from core.team_plan import TeamPlanner

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.loop import AgentLoop


def _handle_team_plan(rest: str, agent: AgentLoop) -> bool:
    del agent
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 5
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
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
        goal_parts.append(token)
        i += 1
    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :team-plan <goal> [--limit N] [--json]", file=sys.stderr)
        return True
    try:
        plan = TeamPlanner().plan(goal, limit=limit)
    except ValueError as exc:
        print(f"Usage: {exc}", file=sys.stderr)
        return True
    if as_json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(plan.user_summary(), file=sys.stderr)
    return True

def _handle_team_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    dry_run = True
    limit = 5
    max_model_calls = 10
    max_cost_units = 20
    goal_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--allow-effects":
            dry_run = False
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
        if token == "--max-model-calls":
            if i + 1 >= len(tokens):
                print("Usage: --max-model-calls requires a number", file=sys.stderr)
                return True
            try:
                max_model_calls = int(tokens[i + 1])
            except ValueError:
                print("Usage: --max-model-calls requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--max-cost-units":
            if i + 1 >= len(tokens):
                print("Usage: --max-cost-units requires a number", file=sys.stderr)
                return True
            try:
                max_cost_units = int(tokens[i + 1])
            except ValueError:
                print("Usage: --max-cost-units requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        goal_parts.append(token)
        i += 1
    goal = " ".join(goal_parts).strip()
    if not goal:
        print(
            "Usage: :team-run <goal> [--allow-effects] [--limit N] "
            "[--max-model-calls N] [--max-cost-units N] [--json]",
            file=sys.stderr,
        )
        return True
    try:
        from core.subagent_runner import SubAgentRunner
        runner = None if dry_run else SubAgentRunner(
            workspace_root=workspace,
            policy=agent.policy,
            model_router=agent.model_router,
            parent_registry=agent.registry,
            log_dir=agent.log.log_dir,
        )
        plan = TeamPlanner().plan(goal, limit=limit)
        executor = TeamExecutor(runner=runner)
        report = executor.run(
            plan,
            dry_run=dry_run,
            budget=TeamBudget(
                max_model_calls=max_model_calls,
                max_cost_units=max_cost_units,
            ),
        )
    except ValueError as exc:
        print(f"Usage: {exc}", file=sys.stderr)
        return True
    event = "team_execution" if not dry_run else "team_execution_dry_run"
    agent.log.log(event, report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True
