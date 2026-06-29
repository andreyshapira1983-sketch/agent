# Commands Map

This map summarizes the current command surface advertised by `:help`.

It is descriptive only. It does not create new commands or change command
behavior.

## Memory

- `:mem | :memory` - inspect working and persistent memory.
- `:smart-memory [--json]` - inspect episodic, procedural, and consolidation
  memory.
- `:memory-consolidate [--json]` - link episodes to reusable procedures now.
- `:clear` - wipe working memory only.
- `:remember [tags] <text>` - save to persistent memory through the write
  policy gate.
- `:forget [id|all]` - delete persistent record(s).

Note: `:remember` currently rejects writes because persistent store is not
configured. Persistent memory setup should be a separate future task.

## Sources

- `:ingest-source <path> [flags]` - ingest one UTF-8 text or code file into the
  Source Registry.
- `:ingest-project [path] [flags]` - ingest project text or code files, with a
  default limit of 80.
- `:source-library [group|all]` - list curated online source families.
- `:source-registry [flags]` - list ingested sources and claim counts.
- `:source-review-plan <goal>` - compare requested files or sources against the
  Source Registry.
- `:implementation-plan <goal>` - produce a local source-backed implementation
  plan.
- `:patch-proposal-plan <goal>` - produce a local read-only patch proposal plan.
- `:ingest-web <topic> [flags]` - search or fetch curated web library sources.
- `:ingest-rss <url> [flags]` - fetch RSS or Atom feed entries into the Source
  Registry.
- `:connectors [status] [--json]` - list source connectors and rough costs.
- `:connector-plan <goal> [flags]` - recommend source connectors for a task.
- `:learn [goal] [flags]` - plan sources, then ingest the selected learning set.
- `:learn-project [goal] [flags]` - alias for `:learn`.
- `:conflicts [--limit N|--json]` - inspect source claim conflicts and
  suggestions.

## Models/Budget

- `:models [--json]` - inspect model routes and registry.
- `:model-registry-audit [--json]` - inspect selected versus available model
  candidates.
- `:model-usage [--json]` - inspect model calls, tokens, and cost units.
- `:operator-budget [--json]` - show a concise budget and model usage digest.
- `:budget-config [--json]` - inspect budget limit config and environment
  overrides.
- `:budget-status` - inspect default autonomous runtime budgets.
- `:budget-window-status [--json]` - inspect persistent hour/day budget windows.

## Operator Status

- `:operator-check [--json]` - show a conversational project/status digest.
- `:urgent-status [--json]` - show approvals, queue, and scheduler urgency.
- `:next-actions [--json]` - show architecture priorities and recommendations.
- `:autonomy-readiness [--json]` - report whether autonomy is safe to run now.
- `:coding-readiness [--json]` - report safe programming task readiness.
- `:operator-task ... :end` - produce one safe multi-line operator task report.
- `:task-begin ... :task-end` - buffer a complex instruction; `:task-abort`
  discards it.
- `:best-next-action [--json]` - choose the single most important next action.
- `:ack <action> [--ttl H] [why]` - acknowledge an advisory alert.
- `:ack-list` - list active acknowledgements.
- `:ack-clear <action>` - restore an acknowledged alert to the top-pick race.
- `:assumptions [--json]` - show the last 20 logged planning assumptions.

## Self-Build/Repair

- `:self-build-propose` - propose a self-build patch or `NO_PATCH`.
- `:patch-proposal-plan <goal>` - produce a read-only patch proposal plan.
- `:rollback [plan_id]` - apply the latest compensation plan, or show plans with
  `list`.
- `:repair <target> <proposal> [tests...] [--pattern PAT]` - guarded
  self-repair with diff, approval, write, tests, and rollback.
- `:propose-repair <target> [tests...] [--pattern PAT] [--trace TRACE]` -
  generate a repair proposal without writing files.

## Autonomy

- `:auto-run [goal] [flags]` - run a bounded autonomous health pass.
- `:work-session [goal] [flags]` - run a bounded multi-cycle session with a time
  budget.
- `:campaign-start [goal] [flags]` - start a budgeted autonomous campaign with a
  per-cycle ledger.
- `:campaign-status [--recent N]` - read the campaign ledger digest without
  spending budget.
- `:auto-status` - inspect autonomous runtime inbox/status.

## Subagents/Team

- `:team-plan <goal> [--json]` - dry-run bounded subagent contracts.
- `:team-run <goal> [--json]` - dry-run execution walk over subagent contracts.
- `:capability-request <goal> [--submit] [--json]` - propose missing connector
  or capability boundaries.
- `:subagent-proposal <goal> [--submit]` - create an autonomous subagent
  initiative proposal.

## Approval

- `:approval-list [status|all]` - list pending, approved, or denied approval
  items.
- `:approval-triage` - read-only triage for clusters, duplicates, stale items,
  and advice.
- `:approval-approve <id>` - mark an approval inbox item approved.
- `:approval-deny <id>` - mark an approval inbox item denied.
- `:approval-run <id>` - execute one approved whitelisted operation.
- `:approval-abort <id>` - mark an approval inbox item aborted.
- `:inbox` - shortcut for listing pending approvals.
- `:approve <id>` - shortcut for `:approval-approve`.
- `:deny <id>` - shortcut for `:approval-deny`.

## Tasks/Scheduler

- `:queue-status` - inspect runtime task queue summary.
- `:scheduler-status` - inspect scheduler summary.
- `:task-add [goal] [flags]` - enqueue a persistent autonomous task.
- `:task-list [status|all]` - list runtime tasks.
- `:task-run [--limit N]` - run due pending runtime task(s).
- `:task-cancel <task_id>` - cancel one queued task.
- `:schedule-add <min> <goal>` - create a recurring scheduler entry.
- `:schedule-list [status|all]` - list schedules.
- `:schedule-tick [--run]` - enqueue due schedule tasks and optionally run them.

## Hygiene/Audit

- `:architecture-audit [--json]` - inspect layers and multi-agent gaps.
- `:state-store-drill [--json]` - prove JSONL quarantine/recovery on an
  isolated file.
- `:release-audit [--json]` - inspect release artifact hygiene exclusions.
- `:supply-chain-audit [--json]` - inspect pinned dependencies and CI release
  gates.
- `:hygiene [subcmd] [--dry-run]` - run memory hygiene operations.

Hygiene subcommands from `:help`:

- `backups` - delete old `.bak.<ts>` files, keeping the last three and only
  touching backups older than 14 days.
- `expire` - drop persistent records past their TTL.
- `dedupe` - collapse near-duplicate persistent records.
- `summarise <tag>` - merge records sharing a tag through the LLM.
- `archive [--threshold=N] [--min-age=N]` - move low-value records to archive.
- no subcommand - run `expire`, then `dedupe`, then `backups`.

