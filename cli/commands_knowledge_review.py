"""Разбор накопленного: конфликты источников и реестр допущений.

Приехало из `cli/commands_misc.py`, которого больше нет. Тот файл сам себя
называл «разное» и держал шесть несвязанных тем: по имени нельзя было узнать,
что внутри, а войти в него приходилось за любой из шести.

Обе команды только ЧИТАЮТ: `:conflicts` показывает противоречия между
источниками, `:assumptions` — последние допущения, которые агент себе
позволил. Приобретение знаний — в `cli/commands_learn.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.conflict_review import ConflictReview

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.loop import AgentLoop


def _handle_conflicts(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    limit = 10
    as_json = False
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
        print(f"Usage: unknown :conflicts option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True

    registry = None
    store = getattr(agent, "source_registry_store", None)
    if store is not None:
        registry = store.load_registry()
    if registry is None or (not registry.sources and not registry.claims):
        registry = getattr(agent, "last_source_registry", None)
    if registry is None:
        print("=== conflicts ===\n(no source registry available)", file=sys.stderr)
        return True

    report = ConflictReview().review(registry)
    agent.log.log("conflict_review", report.to_dict())
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(report.user_summary(limit=limit), file=sys.stderr)
    return True

def _handle_assumptions(rest: str, agent: AgentLoop) -> bool:  # Layer 5
    """Show the most-recent assumptions logged by the Assumption Registry."""
    use_json = "--json" in rest
    store = getattr(agent, "assumption_store", None)
    if store is None:
        print("(assumption store not enabled in this session)", file=sys.stderr)
        return True
    try:
        recent = store.load_recent(20)
    except Exception as exc:
        print(f"(assumption store error: {exc})", file=sys.stderr)
        return True
    if not recent:
        print("(no assumptions recorded yet)", file=sys.stderr)
        return True
    if use_json:
        print(json.dumps([a.to_dict() for a in recent], ensure_ascii=False, indent=2))
        return True
    current_run = getattr(getattr(agent, "log", None), "trace_id", None)
    for a in recent:
        run_tag = " [current]" if a.run_id == current_run else f" [run …{a.run_id[-8:]}]"
        verified_tag = " ✓" if a.verified is True else (" ✗" if a.verified is False else "")
        conf = int(a.confidence * 100)
        print(
            f"  [{a.category}] {a.text} ({conf}%){verified_tag}{run_tag}",
            file=sys.stderr,
        )
    return True
