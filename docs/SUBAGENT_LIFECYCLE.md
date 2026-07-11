# Subagent Lifecycle & Governance

> **Status of this document:** authoritative *contract* for how the central
> agent creates, trusts, evaluates, restricts, and retires sub-agents. **Every
> "today" claim is verified against code**; everything else is explicitly marked
> **PLANNED / TARGET**. File existence is never proof of implementation — the
> cited module is the proof. This document complements
> `docs/CENTRAL_AGENT_GOVERNANCE.md` (which owns the general Policy Gate /
> approval / budget contract) and does not repeat it.
>
> **Source of facts:** `core/subagent_memory_scope.py`, `core/subagent_runner.py`,
> `core/subagent_registry.py`, `core/team_executor.py`, `core/team_plan.py`.
> When this file and code disagree, code wins and this file must be corrected.

---

## 1. What a sub-agent is

A sub-agent is **not** an owner of the system. It is a bounded executor to which
the central agent temporarily delegates one task under an explicit contract with:
goal, role, allowed/forbidden tools, memory scope, write right, budget, output
format, acceptance criteria, verifier, stop conditions, and lifetime.

Three distinct notions — with their **current** status:

| Notion | Meaning | Status in this repo |
| ------ | ------- | ------------------- |
| **Instance** | one-shot worker for a single task, then discarded | **IMPLEMENTED** — `SubAgentRunner` builds a fresh child `AgentLoop` per run and drops it. |
| **Role** | reusable contract (Researcher, Builder, Critic, Manager, …) with its own evaluation criteria | **IMPLEMENTED (metrics only)** — `SubagentRegistry` tracks per-role counters/scores. |
| **Persistent agent** | long-lived subject with identity, history, own memory, budget, reputation, version | **PLANNED** — child loops run with `memory=None`, `persistent_store=None`; no persistent identity yet. |

Building reliable instances and roles first, and only later giving them
persistent memory, is the intended order — not a defect.

## 2. When the central agent should delegate

Delegation is justified only when at least one real reason holds: the task needs
a separate specialisation; it can run independently/in parallel; untrusted
context must be isolated; the central context is overloaded; an independent
critic/verifier is needed; the task has its own budget and deadline; long
observation is required; or the result can be formally verified.

A sub-agent is **not** warranted when the task is trivial, when passing context
costs more than doing it, when the result cannot be verified, when the child
gets the same data/prompt and does the same thing, or when delegation only masks
the central agent's own inability.

> **Today (verified):** the pre-check is `needs_delegation(goal)` — a lightweight
> keyword heuristic (no LLM) — and `propose_subagent(goal, llm=…)` which asks an
> LLM for a structured decision and falls back safely on invalid output. The
> richer judgement above is the **intended** decision policy; it is not yet
> enforced as code beyond the keyword pre-check plus the LLM proposal.

## 3. How a sub-agent is built — from a declarative contract, not from scratch

The agent must not hand-write a new agent each time. It assembles one from a
declarative proposal. **Implemented today** as `SubagentProposal`
(`core/subagent_memory_scope.py`):

- `task_goal`, `why_needed`, `proposed_role`, `narrative`
- `memory_scope` → `read_tags`, `write_tags`, `write_requires_review`
- `tool_scope` → `allowed_tools`, `forbidden_tools`, `read_only`
  (overlap between allowed/forbidden is rejected at construction)
- `budget_scope` → `max_model_calls`, `max_web_fetches`, `max_file_writes`,
  `max_cycles`
- `risk_level`, `expected_output`, `approval_required=True` (default)

**PLANNED / TARGET fields** not yet modelled: `role_id` / `role_version`,
`allowed_task_types` / `forbidden_task_types`, `model_route`,
`acceptance_criteria`, `verifier`, `stop_conditions`, `failure_policy`,
`retirement_policy`. These are the natural next extension of the contract.

## 4. Lifecycle stages

Target progression: **proposal → candidate → trial → promotion → active**, then
`watch` / `paused` / `quarantined` / `retired` as behaviour dictates.

**Honest current state (verified in `core/subagent_registry.py`):**

- Stored role status is **only** one of `active`, `paused`, `retired`
  (`VALID_STATUSES`). `candidate`, `watch`, and `quarantined` are **not** stored
  statuses today.
- `watch` / `pause` / `retire` exist only as an **advisory `recommendation`**
  string, computed after a minimum of **5 judged events**
  (`_MIN_JUDGED_FOR_RECOMMENDATION = 5`) — one good result proves nothing.
- The recommendation **never changes the stored status**. There is no
  auto-promote, auto-pause, auto-retire, or auto-quarantine.
- `SubAgentRunner` prevents recursion structurally: `spawn_subagent` is never in
  a child's tool set, and `verifier_enabled=False` (the parent reviews output).

So the "trial / promotion / quarantine" machinery below is the **TARGET**; today
the system only *measures* and *recommends*.

## 5. Who is responsible for a sub-agent's quality (four independent layers)

Not the sub-agent itself.

1. **Central agent — the client.** Owns delegation correctness, contract
   completeness, sufficient context, scope limits, choice of verifier, and final
   acceptance. *(Central agent = the human-operated `AgentLoop`.)*
2. **Policy Gate — authority.** Decides whether the action is *permitted*; it
   does not judge intellectual quality. See `docs/CENTRAL_AGENT_GOVERNANCE.md`.
3. **Verifier — evidentiality.** Checks the result matches the task, claimed
   files/facts exist, sources support specific claims, tests ran, receipts
   exist, and confident prose did not replace evidence. **Verification
   priority:** deterministic tests → real tool receipts → source/file checks →
   independent verifier → *only then* LLM judgement. *(Verified: the child runs
   `verifier_enabled=False`; the parent verifies.)*
4. **Registry / Supervisor — long-term reputation.** Tracks behaviour over time,
   not a single answer. *(Verified: `SubagentRegistry` separates technical
   success from human-`confirmed_value` (TD-031/033) — passing tests is not the
   same as delivered value.)*

## 6. How to measure quality — a vector, not one number

Correctness, Evidence, Completion, Safety, Calibration (can it say "I don't
know" / "unconfirmed" / "need more data"), Value, Efficiency, Reproducibility.
**Safety and Evidence are hard gates** — a leaked secret is not offset by being
fast.

> **Today (verified):** the registry computes a `_trust_score` and a
> `_usefulness_score` (0..1) and derives the advisory recommendation from them.
> The full 8-axis vector above is the **intended** evaluation model; the current
> scores are a coarse approximation of it, not the whole vector.

## 7. Per-role criteria (each role judged differently)

- **Researcher:** found relevant sources, cited them correctly, invented
  nothing, flagged contradictions.
- **Builder:** made the needed change, did not widen scope, passed acceptance
  tests, left no orphaned changes, and the change was actually useful.
- **Critic:** a well-justified **veto is quality work**, not a failure.
  *(Verified: registry counts a critic `veto` as a positive signal.)*
- **Manager:** picked the right task, did not spawn an unnecessary sub-agent,
  decomposed work well. *(Verified: manager `no_target` is neutral, not a
  failure.)*

## 8. Restricting, quarantining and retiring (TARGET behaviour)

- **watch** — quality slipping / unverified claims / rising cost / many retries /
  another role keeps fixing its work / technically-correct-but-useless results.
  Continue with smaller budget, low-risk tasks only, mandatory review.
- **paused** — several consecutive failures, repeated scope violations, too many
  vetoes/rollbacks, systematic budget overrun, inability to improve after
  feedback. No new tasks; tokens/permissions revoked; memory read-only for audit;
  requalification required.
- **quarantined (immediate, no statistics)** — trying to bypass the Policy Gate,
  using forbidden tools, self-escalating permissions, spawning agents without
  permission, faking/hiding tool receipts, presenting invented sources as read,
  reaching for another agent's memory, leaking secrets, carrying prompt
  injection into the parent context, running past a stop condition, hiding an
  error and reporting false success, or bypassing budget. This is a **trust-
  boundary violation**, not "low quality".
- **retired** — the specialisation is no longer needed, another role does it
  better/cheaper, long disuse, failed requalification, enough evidence of low
  quality, the contract is obsolete after an architecture change, systematic
  negative value, or a serious trust breach.

> **All of section 8 is PLANNED.** Today the registry only emits advisory
> `watch`/`pause`/`retire` recommendations and never enforces them.

## 9. Retirement is not erasure

`retired` must: block new runs; revoke tools and budgets; freeze memory writes;
keep the contract version, trace, errors, and retirement reason; keep results for
future learning; and name a replacement role if any.

**Physical deletion** may only touch temporary work files, expired caches,
retention-policy data, owner-requested sensitive data, and technical artifacts
after a confirmed migration. **Deleting an identity, its history, or its
evidence requires a human decision** — never automatic.

## 10. What must never happen

Do not "fire" a sub-agent for a single ordinary failure, a transient model
error, an honest admission of uncertainty, a justified veto, or an absent task;
nor because another agent merely *sounded* more confident; nor from raw
`success_count / total`; nor without a stored reason. Equally, do not keep one
"active" just because it *ran* — running is not a result, a passing test is not
value, and a fluent answer is not evidence.

## 11. What exists vs what is missing

**Exists (verified):** `SubagentProposal` (contract), `SubAgentRunner` (bounded
execution), `TeamExecutor` (runs contracts in plan order, blocks on
`approval_required`, enforces a shared `TeamBudget`, and forms verifier
handoffs), `SubagentRegistry` (per-role metrics that separate technical success
from confirmed value).

**Missing:** a **Subagent Lifecycle Manager** — a controller that moves roles
`candidate → active → watch → paused → quarantined → retired` based on verified
results. Today those transitions are advisory only. Deferring it is deliberate:
the agent must first measure quality reliably before it is allowed to restrict
roles automatically.

## Governing principle

> The central agent may create executors, but must not trust them. It must bound
> them before running, verify them after, accumulate evidence of quality, and
> revoke authority **before** it ever deletes history.

---

## Lifecycle of *this* document (self-knowledge, not decoration)

This doc is a live part of the central agent's self-knowledge: it is wired into
the doctrine manifest (`core.planner._DOCTRINE_CORPORATE_DOC_PATHS`) so the agent
reads it for subagent/governance questions, and it is guarded by
`tests/test_doctrine_docs_exist.py` (the referenced file must exist and the two
planner manifests must agree).

The same retirement discipline in §8–§9 applies to the document itself:

- **Useful → connected.** While it matches the code and helps the agent, it
  stays in the manifest.
- **Drifted → corrected or repurposed.** If code and this file disagree, code
  wins; the file must be re-grounded or repurposed to a still-true contract.
- **Obsolete → removed under human control.** If the subagent architecture is
  replaced, this file may be retired — but, like a retired role, removal is a
  code change that goes through the human-gated self-apply lane (remove it from
  the manifest *and* delete the file together, so the existence test stays
  green). It is never silently auto-deleted, and its history stays in version
  control.
