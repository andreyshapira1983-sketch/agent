# Repository Entry Point and Navigation

This README is the entry point and short navigation map for the repository. It
does **not** own doctrine or architecture itself; it points at the documents
that do. When a document below conflicts with this README, that document wins.

## Source-of-truth hierarchy

Read these in priority order; a higher entry overrides a lower one:

1. **`AGENT_DOCTRINE.md`** — authoritative rules for how agents must behave.
2. **Target architecture and component boundaries** — the intended design and
   the responsibilities/limits of each component.
3. **`docs/ROADMAP.md`** — the order in which capabilities are developed.
4. **`docs/daemon-progress.md`** — the actual implementation state (which
   sub-items are merged, in review, or not started).
5. **`README.md`** (this file) — entry point and brief navigation only.

## Purpose

The goal of this project is to provide a clear, maintainable foundation with a small, low-risk surface area for change.

## Principles

- Prefer simplicity over cleverness.
- Make behavior explicit and easy to verify.
- Keep changes small and localized.
- Preserve compatibility unless a change is intentionally breaking.
- Update documentation alongside code changes.

## Architecture

- The repository should remain easy to understand at a glance.
- Core behavior should be defined in one primary place whenever possible.
- Supporting code should be organized to reduce duplication and ambiguity.

## Change Process

1. Identify the smallest safe change.
2. Implement the change with minimal impact.
3. Add or update tests when behavior changes.
4. Review for clarity, consistency, and maintainability.

## Testing

Run the project's tests after making changes, and focus on the specific area affected when possible.

## Maintenance

When in doubt, keep this README limited to navigation, and update the
authoritative document (doctrine, architecture, roadmap, or progress) that
actually owns the decision.
