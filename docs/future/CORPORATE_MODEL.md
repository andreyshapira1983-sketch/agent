# Corporate Model — FUTURE / TARGET (not current implementation)

> **⚠️ STATUS: FUTURE / ASPIRATIONAL. This document describes a TARGET model,
> NOT what the code does today.** Nothing here should be read as an implemented
> capability. It lives under `docs/future/` on purpose. For what actually
> exists today, read `docs/ROADMAP.md` and `docs/CENTRAL_AGENT_GOVERNANCE.md`;
> when those and this document disagree about the present, they win.

This is the logical home for the long-horizon "autonomous organisation" idea
(sometimes called Level 5): many specialised agents coordinating under a central
agent, with their own identity, memory, and budget. It is intentionally kept
separate from the current, human-gated single-agent system.

---

## Where we are today (honest baseline)

- **One** central `AgentLoop`, operated by a human through `main.py`.
- Sub-agents are **bounded child loops**, not independent agents: they share the
  parent's Policy Gate and Model Router/budget, carry **no** persistent memory
  or identity, get one planning attempt, and have the verifier disabled — the
  parent reviews their output as a *witness, not a verified source*
  (`core/subagent_runner`).
- Every applied code change is **human-approved** (`:approval-approve` +
  `:self-apply-run`). There is no unattended self-modification.

That baseline is the floor this future model would have to build on **without
weakening** any existing gate.

## Target model (not built)

1. **Per-agent identity.** Each agent has a stable identity, role, and audit
   trail distinct from the central agent.
2. **Per-agent memory.** Isolated persistent + episodic memory per agent, with
   explicit, governed sharing — not a single shared store.
3. **Per-agent budget.** Independent budget envelopes and kill-switches per
   agent, so one agent cannot drain the whole system.
4. **Self-directed coordination.** The central agent delegates, reconciles
   conflicting sub-agent claims, and composes results — beyond today's
   single dry-run `:team-plan` / bounded `:team-run`.
5. **Organisational roles.** Durable specialised roles (research, repair,
   review, synthesis) with performance ledgers that actually gate future
   delegation (`core/subagent_registry` is an early, read-only seed of this).

## Hard invariants any future version must preserve

These are **not** negotiable when this model is eventually built:

- The **Policy Gate** stays a pre-execution checkpoint for every action; no
  agent may widen policy for itself.
- **Human rights** remain reserved: merge, budget kill-switch, and approval of
  escalated/irreversible/external actions stay with a human.
- A sub-agent claim remains a **witness**, verified by a higher authority before
  it is believed or persisted.
- Opus/deep escalation stays **an event, not a habit** — reason-gated, never
  self-authorised.

## When to read this document

This is a corporate-model / long-term-strategy document. It is **not** required
reading for analysing today's code. The planner should only pull it in for
questions specifically about the future corporate/organisational model or
long-horizon autonomy — not for ordinary "how does the current agent work"
questions.

_Source of facts for the "today" sections: `core/subagent_runner`,
`core/team_*`, `core/policy`, `core/approval*`. Everything under "Target model"
is design intent with no backing implementation yet._
