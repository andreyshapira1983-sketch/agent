# Central Agent Governance

> **Status of this document:** authoritative *contract* for the central agent's
> authority and its limits. **Every claim below is derived from code** — no
> capability is described that the code does not enforce. Where a control is
> partial, this document says so. File existence is not proof of implementation;
> the cited module/REPL command is the proof.

The "central agent" is the primary `AgentLoop` (`core/loop`) a human operates
through `main.py` (one-shot `--ask` or the interactive REPL). This document
defines what it may do on its own, what needs approval, and what only a human
may do.

---

## 1. Policy Gate — the checked door (IMPLEMENTED)

Every `Action` passes through the **Policy Gate** (`core/policy`) *before*
execution. It is a pre-execution checkpoint, not an after-the-fact log. Effectful
actions additionally route through the actuation gateway (`core/actuation_gateway`,
`core/gateway_consult`) and leave an append-only receipt (`core/tool_receipts`).

## 2. Governance modes (IMPLEMENTED)

`core/governance` defines five modes and answers "is this kind of autonomous
behaviour allowed in this mode?":

| Mode | Intent |
| ---- | ------ |
| `diagnostic` | inspect only — no writes, no side effects |
| `learning` | read + controlled memory writes on verified evidence |
| `repair` | diagnose + propose/apply a fix **under approval** |
| `improvement` | propose self-improvement changes **under approval** |
| `governance` | change policy / capabilities — highest scrutiny |

Each governed operation (`read_logs`, `run_tests`, `write_memory`,
`propose_diff`, `apply_code_change`, `run_shell`, `add_tool`, `change_policy`,
`enable_external_channel`, …) returns one verdict: **`allow`**,
**`require_approval`**, or **`deny`**. Write/effect operations are never `allow`
in `diagnostic` mode.

## 3. Trust boundary toward sub-agents (IMPLEMENTED, bounded)

The central agent may spawn a sub-agent (`core/subagent_runner`), but it does
**not** extend trust to it:

- A sub-agent's answer is treated as a **witness, not a verified source**. The
  parent's verifier reviews it; the child runs with `verifier_enabled=False`.
- Sub-agents **share** the parent's Policy Gate and Model Router/budget — they
  cannot widen policy or escape the budget.
- Sub-agents carry **no persistent memory or identity** (`memory=None`,
  `persistent_store=None`) and get **one** planning attempt
  (`max_replan_attempts=1`).

So a sub-agent can neither self-approve, self-fund, nor write to long-term
memory. All of that stays with the human-gated central agent.

## 4. Verification before belief (IMPLEMENTED)

- Verifier + confidence gate: `core/verifier*`, `core/confidence_gate`,
  `core/confidence_vector`. Low evidence downgrades the answer
  (`core/low_evidence_policy`) rather than bluffing.
- Reasoning↔action consistency and subsystem disagreement are checked
  (`core/reasoning_action_check`, `core/subsystem_disagreement`).

## 5. Human approval (IMPLEMENTED)

Escalated (irreversible / external) actions require a human decision:

- Default `--auto-approve off` = interactive prompt in the REPL, **deny** in
  one-shot mode. `approve`/`deny` auto-modes exist for tests/scripts only.
- Approval inbox + triage: `core/approval`, `core/approval_inbox`,
  `core/approval_triage`. REPL: `:inbox`, `:approve`/`:deny`,
  `:approval-approve`/`:approval-deny`, `:approval-run`, `:approval-abort`.
- Self-apply of code changes is **always** behind the human lane:
  `:approval-approve <id>` then `:self-apply-run <id>`
  (`core/self_apply_bridge`, `core/self_apply_lane`).

## 6. Deep/Opus escalation is an event, not a habit (IMPLEMENTED)

`core/deep_escalation`: the agent never opens Opus for itself. A deep request
without an explicit `--reason` downgrades to the standard model. Valid reasons
are constrained (e.g. `operator_explicitly_requested_opus`,
`planner_multi_file_architecture_change`).

## 7. Budget authority & kill-switch (IMPLEMENTED)

- Budget governor + persistent windows: `core/budget_governor`,
  `core/budget_ledger`; usage accounting: `core/model_usage`.
- **Persistent kill-switch** for autonomous/daemon execution:
  `core/budget_kill_switch`. REPL: `:budget-kill-switch [--clear]`,
  `:budget-status`, `:budget-window-status`, `:budget-config`.
- Bounded-run guards: `core/circuit_breaker`, `core/termination_guard`,
  `core/step_repetition`, `core/rate_limiter`.

## 8. Memory-write governance (IMPLEMENTED)

Memory writes are policy-checked (`core/memory_policy`) and defended against
self-echo (`core/memory_echo_antibody`). Procedure confidence is smoothed
(`core/smart_memory`) so one success does not mint certainty. Hygiene prunes
stale/duplicate memory (`core/hygiene`, `core/episodic_hygiene`).

---

## 9. Rights reserved to the human (contract)

The following are **not** delegated to the agent and require a human:

- **Merge** of any proposed code change into the repository.
- Flipping the **budget kill-switch** off, or raising budget limits.
- Approving escalated/irreversible/external actions
  (`apply_code_change`, `run_shell`, `enable_external_channel`, `change_policy`,
  `add_tool`).
- Authorising Opus/deep escalation via an explicit reason.

## 10. Honest limits (PLANNED / NOT DONE)

- No unattended self-modification — every applied change is human-approved.
- Sub-agents are bounded child loops, **not** isolated agents with their own
  memory/identity/budget (see `docs/future/CORPORATE_MODEL.md`).
- Governance verdicts are enforced in the modes/operations enumerated in
  `core/governance`; operations outside that enum are not governed by this
  module and fall back to the Policy Gate default.

_Source of facts: `core/policy`, `core/governance`, `core/approval*`,
`core/deep_escalation`, `core/budget_*`, `core/subagent_runner`,
`core/verifier*`, and the `main.py` REPL dispatch. When this file and code
disagree, code wins and this file should be corrected._
