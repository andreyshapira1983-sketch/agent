# Commands Map — operator (REPL) commands

> **Status of this document:** authoritative map of the **real** operator
> command surface. It is built from the actual REPL dispatch in `main.py`
> (the `if head == ":…"` chain) and the CLI flags in `main.py --help`. Only
> commands that exist in code are listed. If a command is not here, it does not
> exist. Aliases are grouped on one row (`a | b`).

Enter these at the interactive REPL (`python main.py` with no `--ask`). A few
are also reachable one-shot via `--ask ":command args"`.

## CLI flags (one-shot / startup) — `main.py`

| Flag | Purpose |
| ---- | ------- |
| `--ask "<q>"` | One-shot question (no memory). Omit to enter the REPL. |
| `--file <path>` | Optional file hint the planner may `file_read`. |
| `--workspace <dir>` | Workspace root (default: current directory). |
| `--auto-approve off\|approve\|deny` | Approval policy for escalated actions (default `off` = deny in one-shot). |
| `--resume <TRACE_ID>` | Resume a previous run by trace id. |
| `--reason <r>` | Deep/Opus escalation reason (with `--ask`). |
| `--expect <e>` | Expected deep output (used with `--reason`). |

## Memory

| Command | Purpose |
| ------- | ------- |
| `:remember [tags] <text>` | Save a fact/preference to persistent memory. |
| `:forget [id\|all]` | Delete persistent record(s). |
| `:clear \| :reset` | Clear working memory (persistent untouched). |
| `:smart-memory \| :memory-status` | Show episodic/procedural memory summary. |
| `:memory-consolidate` | Consolidate episodic memory. |
| `:hygiene [subcmd] [--dry-run]` | Memory hygiene (dedup/expire/summarise). |

## Sources, ingestion & knowledge

| Command | Purpose |
| ------- | ------- |
| `:ingest-source <path>` | Ingest one document/file into the Source Registry. |
| `:ingest-project [path]` | Ingest a project tree. |
| `:ingest-web <topic> [--sources …]` | Controlled web learning from the curated library. |
| `:ingest-rss <feed_url> [--limit N]` | Ingest an RSS feed. |
| `:source-library [group\|all]` | Show the curated online source library. |
| `:source-registry \| :source-status` | Show Source Registry + claims. |
| `:source-review-plan <goal>` | Plan a source review for a goal. |
| `:implementation-plan <goal>` | Plan an implementation from sources. |
| `:patch-proposal-plan \| :patch-plan <goal>` | Plan a patch proposal from sources. |
| `:connectors [all\|wired\|partial\|planned]` | List source connectors. |
| `:connector-plan <goal>` | Plan connector usage for a goal. |

## Models & budget

| Command | Purpose |
| ------- | ------- |
| `:models \| :model-routes` | Show active model routes. |
| `:model-registry-audit \| :model-audit` | Audit model registry / active routes. |
| `:refresh-models \| :model-catalog-refresh \| :model-refresh` | Query providers, persist catalog. |
| `:model-discovery-audit \| :discovery-audit` | Read-only model discovery audit. |
| `:provider-catalog-refresh --dry-run` | Dry-run diff vs live provider models. |
| `:model-usage \| :usage-models` | Model usage ledger. |
| `:budget-status` | Current budget status. |
| `:budget-config \| :budget-limits` | Show budget limits. |
| `:budget-window-status \| :budget-windows \| :budget-ledger` | Budget windows. |
| `:budget-kill-switch \| :kill-switch [--clear]` | Inspect / clear the budget kill-switch. |
| `:operator-budget \| :budget-digest` | Operator-facing budget digest. |

## Approvals & alerts

| Command | Purpose |
| ------- | ------- |
| `:inbox` | List pending approvals. |
| `:approval-list [state]` | List approvals by state. |
| `:approve \| :approval-approve <id>` | Approve an inbox item. |
| `:deny \| :approval-deny <id>` | Deny an inbox item. |
| `:approval-run <id>` | Run an approved item. |
| `:approval-abort <id>` | Abort an approval. |
| `:approval-triage \| :triage` | Read-only triage of the inbox. |
| `:ack \| :acknowledge <action>` | Acknowledge an advisory alert. |
| `:ack-list \| :acks` | List acknowledged alerts. |
| `:ack-clear \| :unack <action>` | Un-acknowledge an alert. |

## Self-improvement (self-build / self-repair) — human-gated

| Command | Purpose |
| ------- | ------- |
| `:repair <target> [proposal] [tests] [--pattern]` | Apply/inspect a self-repair. |
| `:propose-repair <target> [tests] [--pattern] [--trace]` | Generate a repair proposal. |
| `:rollback` | Apply/inspect compensation (undo) plans. |
| `:self-build-produce` | Produce a grounded self-apply proposal (TD-025/036). |
| `:self-build-propose <…>` | Produce a self-build proposal (also one-shot). |
| `:self-build-supervisor` | Read-only self-build supervisor cycle. |
| `:self-split <path.py>` | No-LLM incremental module split proposal. |
| `:self-task-propose` | Stage A: task + failing acceptance test (Ступень 1). |
| `:self-task-build <approval_id>` | Stage B: implement one approved task. |
| `:self-apply-run <inbox_id>` | Apply an approved change (trusted lane). |
| `:self-issue-verify <fingerprint>` | Verify a self-improvement issue. |
| `:value-review <item_id> <verdict> [note]` | Human value-review verdict (TD-032). |
| `:value-review-list` | List value-review items. |

## Autonomous runtime, tasks & scheduling

| Command | Purpose |
| ------- | ------- |
| `:auto-run` | Run one autonomous runtime pass. |
| `:auto-status` | Autonomous runtime status. |
| `:work-session \| :work-sess` | Long work session skeleton. |
| `:campaign-start \| :campaign` | Start a 24/48h work campaign. |
| `:campaign-status \| :campaign-ledger` | Campaign status/ledger. |
| `:queue-status` | Task queue status. |
| `:task-add \| :task-list \| :task-run \| :task-cancel` | Manage the persistent task queue. |
| `:scheduler-status` | Scheduler status. |
| `:schedule-add \| :schedule-list \| :schedule-disable \| :schedule-tick` | Manage scheduled tasks (`:schedule-disable` also one-shot). |
| `:capability-request \| :capability-proposal` | Propose a capability request. |

## Operator status, audits & readiness

| Command | Purpose |
| ------- | ------- |
| `:operator-check \| :project-check \| :project-status` | Project status check. |
| `:operator-task <…>` | Run an operator task. |
| `:learn \| :learn-project` | Learn the current project. |
| `:urgent-status \| :operator-urgent` | Urgent items. |
| `:next-actions \| :operator-next` | Suggested next actions. |
| `:best-next-action \| :next-action \| :bna` | Single most important next action. |
| `:autonomy-readiness \| :operator-readiness` | Autonomy readiness report. |
| `:coding-readiness \| :programming-readiness` | Coding readiness report. |
| `:dry-health-pass` | Read-only health-pass collector. |
| `:architecture-audit \| :arch-audit \| :roadmap-audit` | Static architecture gap audit. |
| `:conflicts \| :conflict-status` | Source Registry conflicts. |
| `:state-store-drill \| :state-drill \| :state-recovery-drill` | State-store recovery drill. |
| `:release-audit \| :release-hygiene` | Release artifact hygiene. |
| `:supply-chain-audit \| :supply-audit \| :ci-audit` | Supply-chain / CI audit. |

## Multi-agent / subagents (bounded — see governance doc)

| Command | Purpose |
| ------- | ------- |
| `:team-plan \| :agent-team \| :subagents <goal>` | Dry-run multi-agent team plan. |
| `:subagent-proposal \| :propose-subagent <…>` | Propose a subagent contract. |
| `:team-run \| :team-execute \| :subagents-run <goal>` | Execute bounded subagent contracts. |

## Help

| Command | Purpose |
| ------- | ------- |
| `:help \| ?` | Show command help. |

_Source of facts: the REPL dispatch in `main.py` and `cli/commands_*.py`
handlers. When a command is added/renamed in `main.py`, update this table.
When this file and `main.py` disagree, `main.py` wins._
