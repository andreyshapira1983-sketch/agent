"""Команды оператора для внешних источников-коннекторов.

Приехало из `cli/commands_misc.py`, которого больше нет. Тот файл сам себя
называл «разное» и держал шесть несвязанных тем: по имени нельзя было узнать,
что внутри, а войти в него приходилось за любой из шести.

`:connectors` показывает зарегистрированные источники, `:connector-plan` —
что будет добыто, до того как что-то добывается.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.source_connectors import (
    SourceConnectorRegistry,
    plan_source_connectors,
    source_connector_payload,
)

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    pass


def _handle_connectors(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    status = "all"
    for token in tokens:
        if token == "--json":
            as_json = True
            continue
        if status != "all":
            print("Usage: :connectors [all|wired|partial|planned] [--json]", file=sys.stderr)
            return True
        status = token.lower()
    if status not in {"all", "wired", "partial", "planned"}:
        print("Usage: :connectors [all|wired|partial|planned] [--json]", file=sys.stderr)
        return True

    registry = SourceConnectorRegistry()
    if as_json:
        payload = source_connector_payload()
        if status != "all":
            payload["connectors"] = [
                item for item in payload["connectors"] if item["status"] == status
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== source connectors ===", file=sys.stderr)
    for connector in registry.list(status=status):
        cost = connector.cost.to_dict()
        print(
            f"  {connector.id} [{connector.status}] cost={cost['cost_class']} "
            f"auth={connector.requires_auth} network={connector.network}",
            file=sys.stderr,
        )
        print(
            "    commands="
            + (", ".join(connector.commands) if connector.commands else "-"),
            file=sys.stderr,
        )
        print(f"    use: {'; '.join(connector.use_when)}", file=sys.stderr)
    return True

def _handle_connector_plan(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 4
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
        print("Usage: :connector-plan <goal> [--limit N] [--json]", file=sys.stderr)
        return True
    plan = plan_source_connectors(goal, limit=limit)
    if as_json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(plan.user_summary(), file=sys.stderr)
    return True
