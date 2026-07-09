# Doctrine and Architecture Source of Truth

This repository's source of truth for doctrine and architecture is intentionally kept in this README.

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

When in doubt, update this document so it continues to reflect the current doctrine and architecture decisions.
