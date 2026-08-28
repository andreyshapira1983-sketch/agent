# Security Policy

## Project shape

This is a single-maintainer research project: an autonomous agent developed and
operated by its owner on a personal machine. There is no released/versioned
product line; the only supported code is the tip of `main`.

| Branch | Supported |
| ------ | --------- |
| `main` | :white_check_mark: |
| anything else | :x: (working branches, may be force-moved) |

## Reporting a vulnerability

Use **GitHub → Security → Report a vulnerability** (private advisory) on this
repository. Please do not open public issues for security problems.

What to include: the file/function, a minimal reproduction, and what an
attacker gains. Expect an acknowledgement within a few days; this is a
one-person project, so timelines are best-effort.

## What counts as a vulnerability here

The agent's own security model is documented in-repo and enforced by tests:

- effects go through a single actuation gateway that fails closed
  (`core/actuation_gateway.py`; invariant I-3 in
  `docs/audit/PROSPECTIVE_AUTONOMY_HAZARD_AUDIT.md`);
- code changes require human approval (`core/self_apply_lane.py`; the lane
  may not edit the audit case files, and judge-touching patches are flagged);
- untrusted text is scanned before it may steer the agent
  (`core/injection_guard.py`);
- secrets never belong in the repository (`.env` is git-ignored; a gitleaks
  scan runs in CI).

Reports that bypass any of these are in scope. So are prompt-injection
escalations that turn observed content into unauthorized effects.

## Non-goals

The agent runs on its owner's machine with its owner's keys. Hardening against
a malicious *operator* is out of scope by design — the operator is the root of
authority (see `knowledge/doctrine/future/CORPORATE_MODEL.md`).
