# Repository Guidelines

## Safety

- Work only with existing files in the current working tree.
- Never inspect `.git`, Git history, or the contents of deleted files.
- `git status --short` is allowed.
- Do not run `git log`, `git show`, `git reflog`, or an unscoped `git diff`.
- Never restore deleted files.
- Never run commit, push, reset, clean, checkout, restore, rebase, or merge without explicit permission.
- Preserve all existing user changes.
- Do not modify files outside this repository.

## Development Workflow

- Before changing code, explain the plan and name the files you intend to modify.
- Make the smallest focused change that solves the task.
- Do not install dependencies or modify lock files without permission.
- Determine test commands only from existing current configuration files. Do not guess.
- Add or update a regression test when fixing a defect.
- Run focused tests first, followed by the complete test suite.
- Run configured formatting, linting, and type checks when available.
- Never claim completion when required tests are failing.
- If a test cannot run, report the exact command, error, and probable cause.
- Finish with a summary of changed files, commands executed, test results, and remaining risks.
