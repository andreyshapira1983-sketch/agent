"""Miscellaneous operator REPL commands (audit / learn / team / connectors).

Split out of ``main.py``. Each command works through ``core`` helpers and the
agent's public surface only — never back into ``main`` — so there is no import
cycle. The runtime task-queue / scheduler helpers (``_task_queue_for`` /
``_scheduler_for``) and their status commands deliberately stay in ``main.py``
because they are shared infrastructure used by many other commands and depend
on ``main`` path constants. ``main.py`` re-exports every name below for the REPL
dispatch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.architecture_audit import audit_architecture
from core.conflict_review import ConflictReview
from core.ingestion import DEFAULT_PROJECT_LIMIT, ingest_files
from core.learning_planner import LearningPlanner
from core.release_hygiene import build_release_manifest
from core.source_connectors import (
    SourceConnectorRegistry,
    plan_source_connectors,
    source_connector_payload,
)
from core.state_store_drill import run_state_store_drill
from core.supply_chain import audit_supply_chain
from core.team_executor import TeamBudget, TeamExecutor
from core.team_plan import TeamPlanner

if TYPE_CHECKING:
    from core.loop import AgentLoop


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
        from core.subagent_runner import SubAgentRunner  # noqa: PLC0415
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
