"""Explicit ``:command`` dispatch for the operator REPL and one-shot mode.

``handle_meta_command`` is the single ordered ``if head == …`` chain that turns a
typed ``:command`` into a call on one of the handler modules. It was extracted
verbatim from ``main.py``. Import it from here -- ``main.py`` re-exported it
until Phase 7 removed the whole compatibility block; the REPL, one-shot and the
suites all reach it through this module now.

It owns *routing only*. Every command's behaviour lives in the module the branch
calls -- ``cli/commands_*.py``, ``app/*_cli.py``, ``app/operator_status.py``,
``cli/help.py``. Nothing here reads or writes agent state directly.

Two things deliberately do **not** live here, and both are documented in
``docs/refactor/CLI_BASELINE.md``:

* the REPL's own block tokens (``:task-begin``/``:task-end``/``:task-abort``,
  ``:operator-task``/``:end``) are intercepted by the REPL loop before dispatch;
* the two pre-``load_dotenv()`` fast paths (``:self-build-propose``,
  ``:schedule-disable``) are handled in ``main()`` before an agent exists.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from app.operator_status import (
    _handle_autonomy_readiness,
    _handle_next_actions,
    _handle_operator_budget,
    _handle_operator_check,
    _handle_programming_readiness,
    _handle_urgent_status,
)
from app.operator_task import _handle_operator_task
from app.runtime_cli import (
    _handle_auto_run,
    _handle_auto_status,
    _handle_campaign_start,
    _handle_campaign_status,
    _handle_work_session,
)
from app.task_scheduler_cli import (
    _handle_queue_status,
    _handle_schedule_add,
    _handle_schedule_disable,
    _handle_schedule_list,
    _handle_schedule_tick,
    _handle_scheduler_status,
    _handle_task_add,
    _handle_task_cancel,
    _handle_task_list,
    _handle_task_run,
    _handle_task_unblock,
)
from cli.commands_approval import (
    _handle_alert_ack,
    _handle_alert_ack_clear,
    _handle_alert_ack_list,
    _handle_approval_abort,
    _handle_approval_decision,
    _handle_approval_list,
    _handle_approval_run,
    _handle_approval_triage,
    _handle_best_next_action,
    _handle_self_issue_verify,
)
from cli.commands_audit import (
    _handle_architecture_audit,
    _handle_release_audit,
    _handle_state_store_drill,
    _handle_supply_chain_audit,
)
from cli.commands_budget import (
    _handle_budget_config,
    _handle_budget_kill_switch,
    _handle_budget_status,
    _handle_budget_window_status,
)
from cli.commands_connectors import _handle_connector_plan, _handle_connectors
from cli.commands_health import _handle_dry_health_pass
from cli.commands_ingest import (
    _handle_implementation_plan,
    _handle_ingest_project,
    _handle_ingest_rss,
    _handle_ingest_source,
    _handle_ingest_web,
    _handle_patch_proposal_plan,
    _handle_self_build_propose,
    _handle_source_library,
    _handle_source_registry,
    _handle_source_review_plan,
)
from cli.commands_knowledge_review import _handle_assumptions, _handle_conflicts
from cli.commands_learn import _handle_learn
from cli.commands_memory import (
    _handle_hygiene,
    _handle_memory_consolidate,
    _handle_rollback,
    _handle_smart_memory,
    _print_persistent,
)
from cli.commands_models import (
    _handle_model_discovery_audit,
    _handle_model_registry_audit,
    _handle_model_usage,
    _handle_models,
    _handle_provider_catalog_refresh,
    _handle_refresh_models,
)
from cli.commands_proposals import (
    _handle_capability_request,
    _handle_subagent_proposal,
)
from cli.commands_repair import (
    _handle_propose_repair,
    _handle_repair,
)
from cli.commands_self_apply import _handle_self_apply_run
from cli.commands_self_build import (
    _handle_self_build_produce,
    _handle_self_build_supervisor,
)
from cli.commands_self_split import _handle_self_split
from cli.commands_self_task import (
    _handle_self_task_build,
    _handle_self_task_propose,
)
from cli.commands_team import _handle_team_plan, _handle_team_run
from cli.commands_value_review import (
    _handle_value_review,
    _handle_value_review_list,
)
from cli.help import render_help
from cli.parsers import _parse_remember

if TYPE_CHECKING:  # annotations only
    from pathlib import Path

    from core.loop import AgentLoop

def handle_meta_command(cmd: str, agent: AgentLoop, workspace: Path) -> bool:
    """Returns True if the command was handled (so the REPL should skip the LLM)."""
    # Any whitespace, not a literal space -- see the comment at the same
    # split in `cli/app.py`. Both paths change together so the two
    # pre-dotenv fast paths cannot diverge from the other 93 commands.
    _parts = cmd.split(maxsplit=1)
    head = _parts[0] if _parts else ""
    rest = _parts[1] if len(_parts) > 1 else ""
    head = head.lower()

    if head in {":mem", ":memory"}:
        if agent.memory is None:
            print("(no working memory in this session)", file=sys.stderr)
        else:
            print(json.dumps(agent.memory.summary(), ensure_ascii=False, indent=2))
        _print_persistent(agent)
        return True

    if head in {":smart-memory", ":memory-status"}:
        return _handle_smart_memory(rest.strip(), agent)

    if head == ":memory-consolidate":
        return _handle_memory_consolidate(rest.strip(), agent)

    if head == ":audit":
        arg = rest.strip().lower()
        if arg in {"on", "enable", "start"}:
            agent.set_audit_read_only(True)
            print(
                "(audit read-only ON — no durable memory writes this session; "
                "agent-auto persistent/semantic writes frozen; :remember still works)",
                file=sys.stderr,
            )
        elif arg in {"off", "disable", "stop"}:
            agent.set_audit_read_only(False)
            print(
                "(audit read-only OFF — durable memory writes resumed)",
                file=sys.stderr,
            )
        elif arg in {"", "status"}:
            state = "ON" if getattr(agent, "audit_read_only", False) else "OFF"
            print(f"(audit read-only: {state})", file=sys.stderr)
        else:
            print("Usage: :audit [on|off|status]", file=sys.stderr)
        return True

    if head in {":clear", ":reset"}:
        if agent.memory is None:
            print("(no working memory to clear)", file=sys.stderr)
        else:
            agent.memory.clear()
            agent.log.log("memory_clear", {"session_id": agent.memory.session_id})
            print("(working memory cleared — persistent memory untouched)", file=sys.stderr)
        return True

    if head == ":remember":
        tags, content = _parse_remember(rest)
        if not content:
            print(
                "Usage: :remember [tag1,tag2] <text>\n"
                "Examples:\n"
                "  :remember I prefer Python over JavaScript\n"
                "  :remember preference,fact I prefer concise Russian answers",
                file=sys.stderr,
            )
            return True
        decision, record = agent.remember(content=content, tags=tags, source="user-explicit")
        if decision.decision == "save" and record is not None:
            print(f"(saved as {record.id}; reasons: {'; '.join(decision.reasons)})", file=sys.stderr)
        else:
            print(f"(rejected: {'; '.join(decision.reasons)})", file=sys.stderr)
        return True

    if head == ":forget":
        target = rest.strip()
        if not target or target.lower() == "all":
            n = agent.forget(record_id=None)
            print(f"(deleted {n} persistent records)", file=sys.stderr)
        else:
            n = agent.forget(record_id=target)
            if n:
                print(f"(deleted {target})", file=sys.stderr)
            else:
                print(f"(no record with id {target})", file=sys.stderr)
        return True

    if head == ":ingest-source":
        return _handle_ingest_source(rest.strip(), agent, workspace)

    if head == ":ingest-project":
        return _handle_ingest_project(rest.strip(), agent, workspace)

    if head == ":source-library":
        return _handle_source_library(rest.strip())

    if head in {":source-registry", ":source-status"}:
        return _handle_source_registry(rest.strip(), agent, workspace)

    if head == ":source-review-plan":
        return _handle_source_review_plan(rest.strip(), agent, workspace)

    if head == ":implementation-plan":
        return _handle_implementation_plan(rest.strip(), agent, workspace)

    if head in {":patch-proposal-plan", ":patch-plan"}:
        return _handle_patch_proposal_plan(rest.strip(), agent, workspace)

    if head == ":self-build-propose":
        return _handle_self_build_propose(rest.strip(), agent, workspace)

    if head == ":self-build-supervisor":
        return _handle_self_build_supervisor(rest.strip(), agent, workspace)

    if head == ":ingest-web":
        return _handle_ingest_web(rest.strip(), agent, workspace)

    if head == ":ingest-rss":
        return _handle_ingest_rss(rest.strip(), agent, workspace)

    if head == ":connectors":
        return _handle_connectors(rest.strip())

    if head == ":connector-plan":
        return _handle_connector_plan(rest.strip())

    if head in {":models", ":model-routes"}:
        return _handle_models(rest.strip(), agent)

    if head in {":model-registry-audit", ":model-audit"}:
        return _handle_model_registry_audit(rest.strip(), agent)

    if head in {":refresh-models", ":model-catalog-refresh", ":model-refresh"}:
        return _handle_refresh_models(rest.strip(), agent)

    if head in {":model-discovery-audit", ":discovery-audit"}:
        return _handle_model_discovery_audit(rest.strip(), agent)

    if head == ":provider-catalog-refresh":
        return _handle_provider_catalog_refresh(rest.strip(), agent)

    if head in {":architecture-audit", ":arch-audit", ":roadmap-audit"}:
        return _handle_architecture_audit(rest.strip(), agent, workspace)

    if head in {":operator-check", ":project-check", ":project-status"}:
        return _handle_operator_check(rest.strip(), agent, workspace)

    if head in {":operator-budget", ":budget-digest"}:
        return _handle_operator_budget(rest.strip(), agent, workspace)

    if head in {":budget-config", ":budget-limits"}:
        return _handle_budget_config(rest.strip(), agent, workspace)

    if head in {":urgent-status", ":operator-urgent"}:
        return _handle_urgent_status(rest.strip(), agent, workspace)

    if head in {":next-actions", ":operator-next"}:
        return _handle_next_actions(rest.strip(), agent, workspace)

    if head in {":autonomy-readiness", ":operator-readiness"}:
        return _handle_autonomy_readiness(rest.strip(), agent, workspace)

    if head == ":dry-health-pass":
        return _handle_dry_health_pass(rest.strip(), agent, workspace)

    if head in {":coding-readiness", ":programming-readiness"}:
        return _handle_programming_readiness(rest.strip(), agent, workspace)

    if head == ":operator-task":
        return _handle_operator_task(rest, agent, workspace)

    if head in {":learn", ":learn-project"}:
        return _handle_learn(rest.strip(), agent, workspace)

    if head == ":auto-run":
        return _handle_auto_run(rest.strip(), agent, workspace)

    if head in {":work-session", ":work-sess"}:
        return _handle_work_session(rest.strip(), agent, workspace)

    if head in {":campaign-start", ":campaign"}:
        return _handle_campaign_start(rest.strip(), agent, workspace)

    if head in {":campaign-status", ":campaign-ledger"}:
        return _handle_campaign_status(rest.strip(), agent, workspace)

    if head in {":capability-request", ":capability-proposal"}:
        return _handle_capability_request(rest.strip(), agent, workspace)

    if head == ":auto-status":
        return _handle_auto_status(agent, workspace)

    if head in {":conflicts", ":conflict-status"}:
        return _handle_conflicts(rest.strip(), agent, workspace)

    if head == ":budget-status":
        return _handle_budget_status(agent, workspace)

    if head in {":budget-window-status", ":budget-windows", ":budget-ledger"}:
        return _handle_budget_window_status(rest.strip(), agent)

    if head in {":budget-kill-switch", ":budget-killswitch", ":kill-switch"}:
        return _handle_budget_kill_switch(rest.strip(), agent, workspace)

    if head in {":state-store-drill", ":state-drill", ":state-recovery-drill"}:
        return _handle_state_store_drill(rest.strip(), agent, workspace)

    if head in {":release-audit", ":release-hygiene"}:
        return _handle_release_audit(rest.strip(), agent, workspace)

    if head in {":supply-chain-audit", ":supply-audit", ":ci-audit"}:
        return _handle_supply_chain_audit(rest.strip(), agent, workspace)

    if head in {":model-usage", ":usage-models"}:
        return _handle_model_usage(rest.strip(), agent)

    if head in {":team-plan", ":agent-team", ":subagents"}:
        return _handle_team_plan(rest.strip(), agent)

    if head in {":subagent-proposal", ":propose-subagent"}:
        return _handle_subagent_proposal(rest.strip(), agent, workspace)

    if head in {":team-run", ":team-execute", ":subagents-run"}:
        return _handle_team_run(rest.strip(), agent, workspace)

    if head == ":approval-list":
        return _handle_approval_list(rest.strip(), agent, workspace)

    if head in {":approval-triage", ":triage"}:
        return _handle_approval_triage(rest.strip(), agent, workspace)

    if head in {":best-next-action", ":next-action", ":bna"}:
        return _handle_best_next_action(rest.strip(), agent, workspace)

    if head == ":self-issue-verify":
        return _handle_self_issue_verify(rest.strip(), agent, workspace)

    if head in {":ack", ":acknowledge"}:
        return _handle_alert_ack(rest.strip(), agent, workspace)

    if head in {":ack-list", ":acks"}:
        return _handle_alert_ack_list(rest.strip(), agent, workspace)

    if head in {":ack-clear", ":unack"}:
        return _handle_alert_ack_clear(rest.strip(), agent, workspace)

    # Short aliases for approval commands
    if head == ":inbox":
        return _handle_approval_list("pending", agent, workspace)

    if head == ":approve":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="approve")

    if head == ":deny":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="deny")

    if head == ":approval-approve":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="approve")

    if head == ":approval-deny":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="deny")

    if head == ":approval-run":
        return _handle_approval_run(rest.strip(), agent, workspace)

    if head == ":self-apply-run":
        return _handle_self_apply_run(rest.strip(), agent, workspace)

    if head == ":self-build-produce":
        return _handle_self_build_produce(rest.strip(), agent, workspace)

    if head == ":self-split":
        return _handle_self_split(rest.strip(), agent, workspace)

    if head == ":self-task-propose":
        return _handle_self_task_propose(rest.strip(), agent, workspace)

    if head == ":self-task-build":
        return _handle_self_task_build(rest.strip(), agent, workspace)

    if head == ":value-review":
        return _handle_value_review(rest.strip(), agent, workspace)

    if head == ":value-review-list":
        return _handle_value_review_list(rest.strip(), agent, workspace)

    if head == ":approval-abort":
        return _handle_approval_abort(rest.strip(), agent, workspace)

    if head == ":queue-status":
        return _handle_queue_status(agent, workspace)

    if head == ":scheduler-status":
        return _handle_scheduler_status(agent, workspace)

    if head == ":task-add":
        return _handle_task_add(rest.strip(), agent, workspace)

    if head == ":task-list":
        return _handle_task_list(rest.strip(), agent, workspace)

    if head == ":task-run":
        return _handle_task_run(rest.strip(), agent, workspace)

    if head == ":task-cancel":
        return _handle_task_cancel(rest.strip(), agent, workspace)

    if head == ":task-unblock":
        return _handle_task_unblock(rest.strip(), agent, workspace)

    if head == ":schedule-add":
        return _handle_schedule_add(rest.strip(), agent, workspace)

    if head == ":schedule-list":
        return _handle_schedule_list(rest.strip(), agent, workspace)

    if head == ":schedule-disable":
        return _handle_schedule_disable(rest.strip(), agent, workspace)

    if head == ":schedule-tick":
        return _handle_schedule_tick(rest.strip(), agent, workspace)

    if head == ":hygiene":
        return _handle_hygiene(rest.strip(), agent, workspace)

    if head == ":rollback":
        return _handle_rollback(rest.strip(), agent, workspace)

    if head == ":repair":
        return _handle_repair(rest.strip(), agent, workspace)

    if head == ":propose-repair":
        return _handle_propose_repair(rest.strip(), agent, workspace)

    if head in {":help", "?"}:
        print(render_help(), file=sys.stderr)
        return True

    if head in {":quit", ":exit"}:
        raise SystemExit(0)

    if head in {":assumptions", ":assumption-log"}:  # Layer 5
        return _handle_assumptions(rest.strip(), agent)

    return False
