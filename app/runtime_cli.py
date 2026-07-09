"""Bounded autonomous runtime REPL handlers (:auto-run, :work-session, :campaign-*).

Split out of ``main.py``. These handlers only orchestrate AutonomousRuntime,
work-session, and campaign flows — they do not own scheduler/task-queue or
approval-inbox logic (those stay in ``cli/commands_approval.py`` and ``main``).

``main.py`` re-exports every name below so existing imports keep working.
"""
from __future__ import annotations

import sys
from pathlib import Path

from cli.commands_approval import _approval_inbox_for
from cli.parsers import _split_meta_args
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.campaign import (
    CampaignConfig,
    CampaignLedger,
    load_ledger_rows,
    run_campaign,
    summarise_ledger,
)
from core.loop import AgentLoop
from core.work_session import WorkSessionConfig, run_work_session


def _handle_auto_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    dry_run = True
    include_tests = True
    include_goal = False
    enable_reflection = False
    include_proposals = False
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
        if token == "--include-goal":
            include_goal = True
            i += 1
            continue
        if token == "--reflect":
            enable_reflection = True
            i += 1
            continue
        if token == "--include-proposals":
            include_proposals = True
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
        if token == "--learning-limit":
            if i + 1 >= len(tokens):
                print("Usage: --learning-limit requires a number", file=sys.stderr)
                return True
            try:
                learning_limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --learning-limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    goal = " ".join(goal_parts).strip() or "project health"
    # --include-goal only makes sense when a real goal was provided
    if include_goal and goal == "project health":
        print(
            "(auto-run: --include-goal requires a goal text, e.g.: "
            ":auto-run --include-goal проверь покрытие тестов)",
            file=sys.stderr,
        )
        return True

    try:
        report = AutonomousRuntime(
            agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        ).run(
            AutonomousRuntimeConfig(
                goal=goal,
                dry_run=dry_run,
                limit=limit,
                include_tests=include_tests,
                include_goal=include_goal,
                learning_limit=learning_limit,
                enable_reflection=enable_reflection,
                include_proposals=include_proposals,
            )
        )
    except Exception as exc:
        print(f"(auto-run failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_work_session(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :work-session command — bounded multi-cycle session."""
    tokens = _split_meta_args(rest)
    dry_run = False
    minutes: float = 10.0
    max_cycles: int = 3
    report_every: int = 1
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
        if token == "--minutes":
            if i + 1 >= len(tokens):
                print("Usage: --minutes requires a number", file=sys.stderr)
                return True
            try:
                minutes = float(tokens[i + 1])
            except ValueError:
                print("Usage: --minutes requires a positive number", file=sys.stderr)
                return True
            i += 2
            continue
        if token in ("--max-cycles", "--cycles"):
            if i + 1 >= len(tokens):
                print(f"Usage: {token} requires a number", file=sys.stderr)
                return True
            try:
                max_cycles = int(tokens[i + 1])
            except ValueError:
                print(f"Usage: {token} requires an integer", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--report-every":
            if i + 1 >= len(tokens):
                print("Usage: --report-every requires a number", file=sys.stderr)
                return True
            try:
                report_every = int(tokens[i + 1])
            except ValueError:
                print("Usage: --report-every requires an integer", file=sys.stderr)
                return True
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    try:
        config = WorkSessionConfig(
            goal=" ".join(goal_parts).strip() or "project health",
            dry_run=dry_run,
            minutes=minutes,
            max_cycles=max_cycles,
            report_every=report_every,
        )
    except ValueError as exc:
        print(f"(work-session config error: {exc})", file=sys.stderr)
        return True

    print(
        f"(work-session goal={config.goal!r} dry_run={config.dry_run} "
        f"minutes={config.minutes} max_cycles={config.max_cycles} "
        f"report_every={config.report_every})",
        file=sys.stderr,
    )

    try:
        result = run_work_session(
            config,
            agent=agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
        )
    except Exception as exc:
        print(f"(work-session failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)
    return True


def _handle_campaign_start(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :campaign-start — a budgeted multi-cycle autonomous campaign.

    Unlike :work-session (a fixed time/cycle health loop), a campaign records a
    per-cycle ledger (goal, best-next-action, cost, result, proposal,
    reason_if_idle) and stops honestly: when the budget is spent, or after N
    consecutive idle cycles where there is no high-priority action to take. An
    idle cycle spends NO model call. Dry-run by default — effects still flow
    only through the existing approval gate.
    """
    tokens = _split_meta_args(rest)
    dry_run = True
    max_cycles = 24
    max_llm_calls = 100
    max_cost_units = 0
    max_idle_streak = 3
    max_wall_clock_seconds = 0
    cycle_pause_seconds = 0
    max_unproductive_streak = 3
    goal_parts: list[str] = []

    def _int_arg(flag: str, index: int) -> int | None:
        if index + 1 >= len(tokens):
            print(f"Usage: {flag} requires a number", file=sys.stderr)
            return None
        try:
            return int(tokens[index + 1])
        except ValueError:
            print(f"Usage: {flag} requires an integer", file=sys.stderr)
            return None

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
        if token in ("--cycles", "--max-cycles"):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_cycles = value
            i += 2
            continue
        if token == "--max-llm-calls":
            value = _int_arg(token, i)
            if value is None:
                return True
            max_llm_calls = value
            i += 2
            continue
        if token == "--max-cost-units":
            value = _int_arg(token, i)
            if value is None:
                return True
            max_cost_units = value
            i += 2
            continue
        if token == "--max-idle":
            value = _int_arg(token, i)
            if value is None:
                return True
            max_idle_streak = value
            i += 2
            continue
        if token in ("--max-wall-clock-seconds", "--max-seconds"):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_wall_clock_seconds = value
            i += 2
            continue
        if token in ("--max-minutes",):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_wall_clock_seconds = value * 60
            i += 2
            continue
        if token in ("--max-hours",):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_wall_clock_seconds = value * 3600
            i += 2
            continue
        if token in ("--cycle-pause-seconds", "--pause-seconds"):
            value = _int_arg(token, i)
            if value is None:
                return True
            cycle_pause_seconds = value
            i += 2
            continue
        if token in ("--max-unproductive-streak", "--max-unproductive"):
            value = _int_arg(token, i)
            if value is None:
                return True
            max_unproductive_streak = value
            i += 2
            continue
        goal_parts.append(token)
        i += 1

    try:
        config = CampaignConfig(
            goal=" ".join(goal_parts).strip() or "project health",
            dry_run=dry_run,
            max_cycles=max_cycles,
            max_llm_calls=max_llm_calls,
            max_cost_units=max_cost_units,
            max_idle_streak=max_idle_streak,
            max_wall_clock_seconds=max_wall_clock_seconds,
            cycle_pause_seconds=cycle_pause_seconds,
            max_unproductive_streak=max_unproductive_streak,
        )
    except ValueError as exc:
        print(f"(campaign config error: {exc})", file=sys.stderr)
        return True

    print(
        f"(campaign goal={config.goal!r} dry_run={config.dry_run} "
        f"max_cycles={config.max_cycles} max_llm_calls={config.max_llm_calls} "
        f"max_cost_units={config.max_cost_units} max_idle={config.max_idle_streak} "
        f"max_wall_clock_s={config.max_wall_clock_seconds} "
        f"cycle_pause_s={config.cycle_pause_seconds})",
        file=sys.stderr,
    )

    ledger = CampaignLedger(path=workspace / "data" / "campaign_ledger.jsonl")
    try:
        result = run_campaign(
            config,
            agent=agent,
            workspace=workspace,
            approval_inbox=_approval_inbox_for(agent, workspace),
            ledger=ledger,
        )
    except Exception as exc:
        print(f"(campaign failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)
    return True


def _handle_campaign_status(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :campaign-status — read the campaign ledger and print a digest.

    Pure read-only operator window over ``data/campaign_ledger.jsonl``: spends
    no budget, creates no agent, touches no state. Answers "what did it do with
    my keys?" by aggregating every recorded cycle (all-time totals, per-result
    counts, distinct goals) and showing the most recent cycles. ``--recent N``
    controls how many trailing rows to show (0 = all).
    """
    tokens = _split_meta_args(rest)
    recent = 10
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--recent":
            if i + 1 >= len(tokens):
                print("Usage: --recent requires a number", file=sys.stderr)
                return True
            try:
                recent = int(tokens[i + 1])
            except ValueError:
                print("Usage: --recent requires an integer", file=sys.stderr)
                return True
            if recent < 0:
                print("Usage: --recent must be >= 0", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"(campaign-status: ignoring unknown argument {token!r})", file=sys.stderr)
        i += 1

    rows = load_ledger_rows(workspace / "data" / "campaign_ledger.jsonl")
    print(summarise_ledger(rows, recent=recent), file=sys.stderr)
    return True
