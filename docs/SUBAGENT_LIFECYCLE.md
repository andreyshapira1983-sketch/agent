# Subagent Lifecycle & Governance

> **Authority.** This is a *normative specification* for how the central agent
> creates, trusts, evaluates, restricts, and retires sub-agents. It is
> **subordinate to `docs/CENTRAL_AGENT_GOVERNANCE.md`** (which owns the general
> Policy Gate / approval / budget contract): where the two overlap, the
> governance doc wins and this file only refines the sub-agent specifics. Where
> this file and code disagree, **code wins and this file must be corrected**.
> File existence is never proof of implementation — the cited module is the proof.
>
> **Source of facts:** `core/subagent_memory_scope.py`, `core/subagent_runner.py`,
> `core/subagent_registry.py`, `core/team_plan.py`, `core/team_executor.py`.
>
> **Current implementation (verified today):** bounded proposal contract; a fresh
> child `AgentLoop` per run with its own trace, a safe tool subset, no persistent
> or episodic memory, and structural no-recursion; a separate team-contract model
> with declared budgets, a verifier name and stop conditions; a team executor
> that reserves budget, blocks `approval_required`, and forms verifier handoffs;
> and a registry that tracks per-role metrics and emits *advisory* recommendations
> separating technical success from human-confirmed value.
>
> **Target lifecycle (planned):** a single canonical lifecycle contract that
> travels unchanged from proposal → execution → verification → reputation →
> retirement, driven by a Subagent Lifecycle Manager.
>
> **Non-implemented capabilities (planned, not in code):** automatic status
> transitions; the stored statuses `candidate` / `watch` / `quarantined`;
> persistent sub-agent identity and long-term memory; a per-sub-agent cost ledger
> reconciled against actual usage; automatic quarantine; requalification; and
> retirement that revokes authority. **`auto-pause` and `auto-retire` do not
> exist today.**

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

The central agent itself is the **top-level (parent) `AgentLoop`**, operating
under human-owned governance, approval, budget and kill-switch authority. It may
run interactively (REPL / one-shot) or through an autonomous runtime; either way
the human owns the governance boundary.

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

**Not modelled in `SubagentProposal`:** `role_id` / `role_version`,
`allowed_task_types` / `forbidden_task_types`, `acceptance_criteria`,
`failure_policy`, `retirement_policy`.

**Partially modelled elsewhere (verified):** a *second* contract type,
`team_plan.SubagentContract`, already carries `model_role`, required `outputs`,
`verifier`, `stop_conditions`, `max_iterations`, and model/cost budgets
(`max_model_calls`, `max_cost_units`). So the fields are not missing from the
system — they live on a different object. **The real architectural gap is that
`SubagentProposal` and `SubagentContract` are not yet unified into one canonical
lifecycle contract.** Unifying them is the natural next extension.

> **Current wiring limitation (verified):** `SubagentProposal`,
> `SubagentContract`, and `SubAgentRunner` are separate layers, and not every
> declared field is enforced end-to-end. `SubAgentRunner.run` receives only
> `contract_name`, `role`, `objective`, `context`, and `allowed_tools`; it then
> intersects `allowed_tools` with a fixed safe set (`_SAFE_SUBAGENT_TOOLS`) and
> runs statelessly. The proposal's `memory_scope` is **not** applied to the child
> loop — it runs with `memory=None` and `persistent_store=None`. Enforcing
> proposal memory and lifecycle policy end-to-end is a future acceptance
> criterion for the Lifecycle Manager.

## 4. Lifecycle stages

Target progression and the authority each transition needs:

| Transition | Trigger | Authority |
| ---------- | ------- | --------- |
| proposed → candidate | contract validated (and, for risky rights, human approval) | Policy Gate + human for risk |
| candidate → active | several verified runs, not one lucky pass | evidence + supervisor |
| active → watch | accumulated weak signals | automatic (planned) |
| watch → paused | sustained degradation | automatic or supervisor (planned) |
| any → quarantined | trust-boundary violation | immediate, automatic (planned) |
| paused/quarantined → retired | after audit | **human-approved** |
| paused → active | requalification only | supervisor + evidence |
| retired → active | a **new version** of the role, never silent revival | human, as new contract |

**Honest current state (verified in `core/subagent_registry.py`):**

- Stored role status is **only** one of `active`, `paused`, `retired`
  (`VALID_STATUSES`). `candidate`, `watch`, and `quarantined` are **not** stored
  statuses today.
- `keep` / `watch` / `pause` / `retire` exist only as an **advisory
  `recommendation`** string, computed after a minimum of **5 judged events**
  (`_MIN_JUDGED_FOR_RECOMMENDATION = 5`) — one good result proves nothing.
- The recommendation **never changes the stored status**. There is no
  auto-promote, auto-pause, auto-retire, or auto-quarantine.
- `SubAgentRunner` prevents recursion structurally: `spawn_subagent` is never in
  a child's tool set, and `verifier_enabled=False` (the parent reviews output).

So the trial / promotion / quarantine machinery below is the **TARGET**; today
the system only *measures* and *recommends*.

## 5. Who is responsible for a sub-agent's quality (four independent layers)

Not the sub-agent itself.

1. **Central agent — the client.** Owns delegation correctness, contract
   completeness, sufficient context, scope limits, choice of verifier, and final
   acceptance.
2. **Policy Gate — authority.** Decides whether the action is *permitted*; it
   does not judge intellectual quality. See `docs/CENTRAL_AGENT_GOVERNANCE.md`.
3. **Verifier — evidentiality.** Checks the result matches the task, claimed
   files/facts exist, sources support specific claims, tests ran, receipts
   exist, and confident prose did not replace evidence. **Verification
   priority:** deterministic tests → real tool receipts → source/file checks →
   independent verifier → *only then* LLM judgement.
   > **Verified nuance:** in the normal parent `AgentLoop` path the child runs
   > `verifier_enabled=False` and the **parent's** verification system reviews
   > child claims. In the standalone `TeamExecutor` path the executor currently
   > **creates `VerifierHandoff` records but does not itself run the verifier** —
   > a handoff is an *obligation to verify*, not proof that verification happened.
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
  *(Verified: registry counts a critic `veto` as a positive signal, while a
  Builder whose output is vetoed is scored down.)*
- **Manager:** picked the right task, did not spawn an unnecessary sub-agent,
  decomposed work well. *(Verified: manager `no_target` is neutral, not a
  failure.)*

The consequence: the **same event means opposite things by role.** A veto raises
a Critic's standing and lowers a Builder's. No single formula fits all roles, so
the registry is role-aware by design.

## 8. Restricting, quarantining and retiring (TARGET behaviour)

- **watch** — quality slipping / unverified claims / rising cost / many retries /
  another role keeps fixing its work / technically-correct-but-useless results.
  Continue with smaller budget, low-risk tasks only, mandatory review.
- **paused** — several consecutive failures, repeated scope violations, **repeated
  vetoes against this role's own outputs, repeated rollbacks, or repeated
  verifier rejection** (note: a Critic *issuing* justified vetoes is the opposite
  — a positive signal), systematic budget overrun, or inability to improve after
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

**Quarantine and retirement are not the same decision.** A trust-boundary
violation warrants **immediate, automatic quarantine** — fast revocation of
authority and preservation of evidence. But the **irreversible** step (final
`retired`, or changing the persistent role) must go through
**incident review and human approval**: `violation → quarantine → evidence
preserved → review → human-approved restore / replace / retire`. An automatic
system may bound a role quickly; it must not make the permanent personnel
decision alone.

> **All of section 8 is PLANNED.** Today the registry only emits advisory
> `watch` / `pause` / `retire` recommendations and never enforces them.

## 9. Retirement is not erasure

`retired` must: block new runs; revoke tools and budgets; freeze memory writes;
and **keep** the contract version, the retirement reason(s), the trace, the
metrics, known errors, a named replacement role if any, and the date/author of
the decision. Retained evidence is what later learning depends on.

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

**Exists (verified):**
- `SubagentProposal` — the bounded proposal contract.
- `SubAgentRunner` — bounded, stateless execution (fresh child loop, safe tool
  subset, no memory, no recursion, one attempt).
- `team_plan.SubagentContract` / `TeamPlanner` — a second contract model with
  `model_role`, `outputs`, `verifier`, `stop_conditions`, `max_iterations`, and
  budgets.
- `TeamExecutor` — runs contracts in plan order, blocks on `approval_required`,
  and performs **conservative budget admission/reservation** using each
  contract's *declared* maxima (it adds `max_model_calls` / `max_cost_units` per
  contract), and forms `VerifierHandoff` records. Actual provider usage is
  accounted through the shared `ModelRouter` / usage ledger; **per-sub-agent
  actual-cost reconciliation is not yet part of the lifecycle registry.**
- `SubagentRegistry` — per-role metrics that separate technical success from
  confirmed value, emitting advisory recommendations only.

**Missing:**
- One **canonical lifecycle contract** unifying `SubagentProposal`,
  `SubagentContract`, runtime enforcement, verification and reputation.
- A **Subagent Lifecycle Manager** — a controller that actually moves roles
  `candidate → active → watch → paused → quarantined → retired` based on verified
  results. Today those transitions are advisory only.

Deferring both is deliberate: the agent must first measure quality reliably, and
enforce proposal fields end-to-end, before it is allowed to restrict roles
automatically.

## Governing principle

> The central agent may create executors, but must not trust them. It must bound
> them before running, verify them after, accumulate evidence of quality, and
> revoke authority **before** it ever deletes history.

---

## Lifecycle of *this* document (self-knowledge, not decoration)

This doc is a live part of the central agent's self-knowledge. It is **not** in
the universal doctrine manifest; it is wired into a **thematic, conditional**
group (`core.planner._SUBAGENT_GOVERNANCE_DOC_PATHS`) so the agent reads it only
when a question is actually about sub-agents / delegation / team execution / role
trust / quarantine / pause / retire / lifecycle — not on every architecture
question. It is guarded by `tests/test_doctrine_docs_exist.py` (the file must
exist, and the manifests stay consistent).

The same retirement discipline in §8–§9 applies to the document itself, and the
agent must not decide "this is obsolete" on an LLM hunch. It needs **observable
signals**: a manifest path that no longer exists; the doc contradicting code; the
doc contradicting a more authoritative source; a test or architecture audit that
records drift; a described capability that was removed; or another document
officially taking over this contract.

Given such a signal, the allowed actions are **correct**, **repurpose**,
**deprecate**, **disconnect from the manifest**, or **propose removal** — never
silent auto-deletion. Removal first takes the file out of routing, marks it
deprecated, checks inbound references, and only then proposes deletion through the
ordinary **human-gated self-apply lane** (remove it from
`_SUBAGENT_GOVERNANCE_DOC_PATHS` *and* delete the file together, so the existence
test stays green). Its history always remains in version control.
