# Idea Notebook

This notebook is a parking lot for future architecture ideas. It is not a task
queue, roadmap commitment, or approval source.

## Notebook Rule

Ideas written here do not become tasks without explicit user approval.

Before any idea turns into implementation work, the agent must ask for approval
and restate:

- the concrete task;
- the files or systems likely to change;
- the expected cost and budget risk;
- the rollback or stop condition;
- whether subagents, external tools, or persistent state writes are involved.

## Agent as Windows Interface

The agent can eventually become a Windows-facing control surface instead of
only a PowerShell workflow.

Future interface ideas:

- dashboard for status, tasks, approvals, budget, models, and logs;
- buttons for safe local commands such as status, readiness, and dry-run checks;
- approval inbox with clear risk labels and one-action execution;
- notifications for budget pressure, stuck tasks, pending approvals, and failed
  autonomous cycles;
- local-first ergonomics, with the agent core remaining testable and usable
  without the GUI.

The GUI is a future layer over a stable core, not a reason to bypass existing
approval, budget, and audit boundaries.

## Agent as Small Corporation

The long-term shape can resemble a small corporation:

- departments map to durable capability areas;
- agents or subagents act like scoped workers;
- the central agent sets priorities, reviews outputs, and manages risk;
- budgets, ledgers, approvals, and audit logs are treated as operating controls;
- work is split into proposals, execution, verification, reporting, and learning.

This idea is about structure and accountability. It should not imply unlimited
autonomy or automatic expansion.

## Central Agent as Manager

The central agent should become less like a single worker doing everything and
more like a manager of bounded workflows.

Responsibilities:

- choose what matters next;
- route work to tools, commands, or subagents;
- enforce budget and approval boundaries;
- verify evidence before accepting results;
- stop or escalate when risk, cost, or uncertainty is too high;
- summarize decisions back to the user.

The central agent remains accountable for coordination, even when subagents
produce useful work.

## Subagents

Subagents are future specialist workers with narrow contracts.

Expected properties:

- explicit objective;
- limited tool scope;
- limited memory scope;
- limited budget;
- clear evidence requirements;
- result returned to the central agent for review;
- no durable authority unless separately approved.

Subagents should be cheap to create, easy to inspect, and safe to discard.

## Finance Agent

A future finance agent could monitor and explain money flows around the system.

Possible scope:

- API usage and projected spend;
- subscriptions and recurring tool costs;
- budget windows and burn rate;
- alerts when spending patterns change;
- simple reports for daily, weekly, and monthly usage;
- recommendations for cheaper models or deferred work.

The finance agent must not execute payments, change billing settings, raise
limits, or enable recharge without explicit approval.

## Model Router

The model router decides which model class fits a task.

Future routing signals:

- task complexity;
- required reasoning depth;
- latency needs;
- evidence and citation requirements;
- privacy or local-only constraints;
- cost ceiling;
- fallback behavior when a preferred model is unavailable.

The router should make the cheap path easy, while preserving high-quality paths
for tasks that actually need them.

## Budget Governor

The budget governor is the spending brake.

Future responsibilities:

- hard session, hour, day, and campaign limits;
- per-command and per-subagent cost ceilings;
- alerts before expensive operations;
- automatic stop on runaway loops;
- ledger summaries that explain what spent the budget;
- refusal to start work when the remaining budget is insufficient.

Budget control should be enforced by code and visible in the operator surface.

## Self-Build Layer

The self-build layer lets the agent improve its own codebase safely.

Expected sequence:

1. identify a problem;
2. propose a patch;
3. explain risk and files affected;
4. wait for approval before applying;
5. run targeted tests;
6. produce a rollback path;
7. report what changed.

Self-build is not permission to silently rewrite the system. It is a controlled
repair and improvement workflow.

