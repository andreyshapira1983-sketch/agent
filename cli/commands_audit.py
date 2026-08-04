"""Команды оператора для аудитов: архитектура, поставки, хранилище, релиз.

Приехало из `cli/commands_misc.py`, которого больше нет. Тот файл сам себя
называл «разное» и держал шесть несвязанных тем: по имени нельзя было узнать,
что внутри, а войти в него приходилось за любой из шести.

Четыре разных аудита, но одно занятие: спросить у системы отчёт о ней самой и
напечатать его. Ни один из них ничего не меняет — это чтение.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.architecture_audit import audit_architecture
from core.release_hygiene import build_release_manifest
from core.state_store_drill import run_state_store_drill
from core.supply_chain import audit_supply_chain

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.loop import AgentLoop


def _handle_architecture_audit(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 8
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
        print(f"Usage: unknown :architecture-audit option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True
    audit = audit_architecture(workspace)
    agent.log.log("architecture_audit", audit.to_dict())
    if as_json:
        print(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(audit.user_summary(limit=limit), file=sys.stderr)
    return True

def _handle_supply_chain_audit(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :supply-chain-audit [--json]", file=sys.stderr)
        return True
    report = audit_supply_chain(workspace)
    agent.log.log("supply_chain_audit", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True

def _handle_state_store_drill(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :state-store-drill [--json]", file=sys.stderr)
        return True
    report = run_state_store_drill(workspace)
    agent.log.log("state_store_recovery_drill", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True

def _handle_release_audit(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :release-audit [--json]", file=sys.stderr)
        return True
    report = build_release_manifest(workspace).report()
    agent.log.log("release_hygiene", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(), file=sys.stderr)
    return True
