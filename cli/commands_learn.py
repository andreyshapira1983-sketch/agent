"""Команда `:learn` — приобретение знаний из файлов проекта.

Приехало из `cli/commands_misc.py`, которого больше нет. Тот файл сам себя
называл «разное» и держал шесть несвязанных тем: по имени нельзя было узнать,
что внутри, а войти в него приходилось за любой из шести.

Отделена от команд разбора знаний (`cli/commands_knowledge_review.py`)
намеренно: эта ПИШЕТ — читает файлы проекта и заводит записи, — а те только
показывают уже накопленное. Разные глаголы, разная цена ошибки.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.ingestion import DEFAULT_PROJECT_LIMIT, ingest_files
from core.learning_planner import LearningPlanner

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.loop import AgentLoop


def _handle_learn(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = DEFAULT_PROJECT_LIMIT
    root = "."
    goal_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
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
        if token == "--root":
            if i + 1 >= len(tokens):
                print("Usage: --root requires a path", file=sys.stderr)
                return True
            root = tokens[i + 1]
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    goal = " ".join(goal_parts).strip()
    try:
        plan = LearningPlanner().plan(
            workspace=workspace,
            goal=goal,
            root=root,
            limit=limit,
        )
        agent.log.log("learning_plan", plan.to_log_payload())
        print(plan.user_summary(), file=sys.stderr)
        if not plan.source_paths:
            return True
        report = ingest_files(
            agent=agent,
            workspace=workspace,
            paths=plan.source_paths,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(learn failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True
