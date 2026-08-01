# AGENT_DOCTRINE.md

## Purpose

This document is the source of truth for how agents should behave in this repository. It defines the operating doctrine, decision priorities, and expectations for changes.

## Core Principles

1. **Preserve correctness first**
   - Do not introduce behavior changes unless explicitly requested.
   - Prefer small, low-risk edits that are easy to review.

2. **Keep changes minimal**
   - Modify only what is necessary to satisfy the task.
   - Avoid unrelated refactors, formatting churn, or dependency changes.

3. **Respect existing architecture**
   - Follow established patterns, naming, and conventions already present in the codebase.
   - If multiple approaches exist, choose the one most consistent with the surrounding code.

4. **Treat this file as authoritative**
   - When making decisions about style, structure, or workflow, defer to this doctrine unless explicitly overridden by a higher-priority instruction.

## Change Discipline

- Make the smallest viable change.
- Prefer additive changes over destructive ones.
- Avoid broad rewrites unless specifically requested.
- If a task is ambiguous, choose the least surprising implementation.

## Validation Expectations

- When practical, validate the affected area with targeted tests.
- Prefer focused test execution over full-suite runs.
- If tests are not run, ensure the change remains clearly low-risk and explain why.

## Communication Expectations

- Be concise and factual.
- Clearly state what changed and why.
- Highlight any assumptions or limitations relevant to the change.

## Priority Order

1. Explicit instructions from the **operator** — the person who owns the task
2. The **task contract** — what the task itself explicitly requires or forbids
3. Behaviour pinned by a **passing test**
4. Repository-wide doctrine and architecture guidance
5. Local code conventions and existing implementation patterns
6. **Advisors without authority** — code review (human or bot), linters, "this
   would be more convenient" suggestions
7. Minimal-risk implementation choices

> **Revised 2026-08-01.** Levels 2, 3 and 6 were added. The previous list had a
> single "explicit user instructions" line and no notion of an advisor, so a
> confidently worded review comment outranked the specification it contradicted.

## Conflicting Instructions

When two sources demand incompatible things of the same subject, **stop** — do
not pick a winner and continue, even when the ranking above makes the winner
obvious. Change nothing, report the conflict with both sources quoted, and let
the operator decide. The full rule, the authority table, and the required
six-point report: [INSTRUCTION_AUTHORITY.md](INSTRUCTION_AUTHORITY.md).

## Maintenance

- Update this document when repository doctrine or architectural expectations change.
- Keep it short, stable, and practical.
