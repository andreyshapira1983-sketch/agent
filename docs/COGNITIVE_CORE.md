# The cognitive core of this agent — what it is, proven from the code

**Status:** analysis, 2026-07-26. No code was moved or written for this document.
It answers one question: *which part of this system is the cognitive core, and how
do we know?* Everything below was read out of the repository at
`main` @ `eec6507`; every claim names the file that backs it.

> **Reconciled 2026-07-27 after PR #177.** This is a mixed document: dated
> findings and amendments sit next to a present-tense description of the current
> architecture. The present-tense parts (§4 boundary, §6 gate stack, §8.5, §8.7,
> §8.11, §10 finding 3, §12) were re-grounded against the code after the sensor
> work landed. Where an older sentence names `core/confidence_gate.py` <!-- historical-ref -->,
> it is **narrating the rename** and is marked as such on its own line; the live
> module is `core/evidence_support.py`.

## 0. How to read this — the rule every claim obeys

A claim earns a place here only if it passes three checks:

1. **Plain meaning** — one sentence a non-programmer can act on.
2. **Why it is core** — which decision it makes. If it stores, executes or
   displays rather than decides, it is not core, however important it is.
3. **How it looks here, and how we prove it** — the file, and the test that
   would fail if it broke.

Every mechanism carries a status, and the status is the honest part:

| status | meaning |
|---|---|
| **ENFORCING** | it can change what the run does — deny, downgrade, pause, stop |
| **OBSERVING** | it detects the condition and writes it to the journal, but nothing changes |
| **ABSENT** | the core needs it; the repository does not have it |

**AMENDMENT (2026-07-26): the status above is only half of the question, and the
missing half is the one that bit.** A mechanism can be ENFORCING — correct
whenever it fires — and still almost never fire. This document's first pass
audited *precision* (does the guard decide correctly?) and never *recall* (does
it fire when it should?). Sections 8.1 and 8.11 below carry the corrections, and
they came from a live session, not from re-reading the code:

| dimension | question | audited in pass 1 |
|---|---|---|
| precision | when it fires, is the decision right? | yes |
| **recall** | **does it fire on the inputs it exists for?** | **no — measured below** |

Every "ENFORCING" verdict in section 8 should be read as *precision only* until a
recall number sits next to it.

The distinction matters more than any diagram: a system full of OBSERVING
mechanisms *looks* defended and is not.

---

## 1. What the cognitive core is

**Plain meaning.** The core is the part that decides **what happens next in a
cycle, on what evidence, at what cost, and when to stop**. It is the part that
can say *no* — to the model, to a tool, to itself.

Everything else in the repository does one of four other jobs: it **remembers**
(memory), it **runs** (runtime/daemon/scheduler), it **acts on the world**
(tools), or it **talks to the operator** (CLI/API). Those four can be replaced
without changing how the agent thinks. The core cannot.

**Why this definition and not "the loop".** `core/loop.py` is where most of the
core executes, but it is not the core: it is ~~3882~~ **4047 (2026-08-02,
after the piece-by-piece extraction of #217–#224)** lines of orchestration that
*calls* the deciders. The deciders are separate, pure, and testable —
`core/policy.py`, `core/replan.py`, `core/deep_escalation.py`,
`core/evidence_support.py` and about thirty more. The core is that set of
deciders plus the sequence in which the loop consults them.

---

## 2. The membership test

A module belongs to the cognitive core when all three are true:

1. **It decides.** Its output changes the path of the run — which branch, which
   model, whether to continue. A module that returns data for someone else to
   decide with is not core.
2. **It can decide without the LLM.** Every core decision must remain available
   when the model is unavailable, slow, expensive or wrong. A decider that
   *needs* the model to function is not a guard, it is a second opinion.
3. **It produces a reason, and the reason is recorded.** A decision with no
   recorded ground cannot be audited, reproduced or argued with.

Applying this test to the repository is what produced the boundary in §4.

Two consequences worth stating, because they are easy to get wrong:

- The **planner is not the core**; it is the core's *supplier*. It proposes.
  The core admits, edits or rejects the proposal (`plan_tool_drop`,
  `plan_parse_failed` in `core/loop.py`).
- The **verifier is core**, even though it uses evidence produced elsewhere,
  because its verdict changes what happens (replan, or an honest low-confidence
  answer).

---

## 3. The decisions only the core makes

These ten decisions exist nowhere else in the system. This list *is* the core's
job description.

| # | Decision | Where it is made today |
|---|---|---|
| D1 | Do we need the model at all for this input? | `core/strategy_router.py`, `core/operator_intent.py`, episodic fast path in `core/loop_gates.py:123-189` (уехало из `core/loop.py` при разборе на модули; прежний якорь — строка 862 того файла — к тому времени уже указывал не туда и держался только тем, что попадал в диапазон) |
| D2 | Is the request understood well enough to start? | `core/clarification_gate.py`, `core/clarification_policy.py`, `core/operational_domain.py` |
| D3 | What is the plan, and is this plan admissible? | `core/planner.py` proposes; `core/loop.py` admits (`plan_parse_failed`, `plan_tool_drop`) |
| D4 | May this specific action execute? | `core/policy.py`, `core/actuation_gateway.py`, `core/approval.py` |
| D5 | Which model tier, at what price? | `core/model_router.py`, `core/deep_escalation.py`, `core/role_router.py` |
| D6 | Is the produced answer actually supported? | `core/verifier.py`, `core/evidence.py`, `core/evidence_support.py` |
| D7 | Continue, replan, ask, or stop? | `core/replan.py`, `core/termination_guard.py`, `core/completion_marker.py` |
| D8 | What may be written to durable memory? | `core/memory_policy.py`, `core/knowledge_use_policy.py` |
| D9 | Must the cycle pause and become resumable? | `core/model_usage.py` (`ModelBudgetExceeded`) → `core/checkpoint.py` → `app/budget_guard.py` |
| D10 | What is recorded as the reason for all of the above? | `core/logger.py` + 71 event kinds emitted from `core/loop.py` |

**How to prove this list is complete and exclusive:** for each decision, grep
for the deciding construct outside `core/`. Today that grep is clean — `core/`
imports nothing from `cli/`, `app/` or `main` (verified 2026-07-26), while 40
modules under `cli/`, `app/`, `tools/`, `api/` import *from* `core/`. The
dependency arrow already points the right way; §11 turns that from a fact into
a guarded rule.

---

## 4. The boundary: what is in, what is out

### In the core

| Group | Modules |
|---|---|
| Routing without the model | `strategy_router`, `operator_intent`, `operator_intent_patterns`, `intent_understanding`, `task_complexity` |
| Understanding & scope | `clarification_gate`, `clarification_policy`, `operational_domain`, `referent_resolver`, `assumption_registry` |
| Planning admission | the plan-validation path in `loop.py`, `reasoning_action_check`, `prompt_registry` |
| Action permission | `policy`, `actuation_gateway`, `gateway_consult`, `approval`, `approval_triage`, `governance` |
| Cost & model choice | `model_router`, `role_router`, `deep_escalation`, `model_usage`, `budget_ledger`, `budget_governor`, `budget_kill_switch`, `evidence_budget` |
| Truth control | `verifier*`, `evidence`, `evidence_support`, `confidence_vector`, `low_evidence_policy`, `unsupported_claims`, `truth_hype_filter`, `subsystem_disagreement`, `completion_obligation`, `structured_facts` |
| Continue / stop | `replan`, `termination_guard`, `step_repetition`, `circuit_breaker`, `completion_marker`, `synth_resilience` |
| Run state | `run_context`, `checkpoint`, `state_integrity` |
| Durable-write permission | `memory_policy`, `knowledge_use_policy`, `memory_echo_antibody` |
| Adversarial defence | `injection_guard`, `data_classifier`, `redaction`, `dlp`, `secret_scanner` |
| The journal | `logger`, `tool_receipts`, `incident` |

### Out of the core, and why

| Zone | Examples | Why it is not core |
|---|---|---|
| **Memory** | `core/memory.py`, `persistent_memory`, `smart_memory`, `episodic_hygiene`, `compactor` | Stores and retrieves. It supplies evidence to decisions but makes none. The *policy* over memory (`memory_policy`) is core; the store is not. |
| **Runtime** | `core/autonomous_runtime.py`, `scheduler`, `task_queue`, `work_session`, `campaign*`, `app/daemon.py`, `app/worker_pool.py` | Decides *when* and *how often* a cycle runs, not what the cycle concludes. It consumes core budgets rather than defining them. |
| **Tools** | `tools/*`, `core/source_connectors`, `ingestion*` | Act on the world. Their risk classification is core (`tool.risk_for` consumed by `policy.py`); their execution is not. |
| **Interfaces** | `cli/*`, `api/server.py`, `main.py` | Turn operator input into calls and results into text. After the Phase-7 refactor `main.py` is 47 lines and holds no decision at all. |
| **Producers** | `self_build_producer`, `self_task_producer`, `repair_proposal`, `subagent_runner` | They generate candidate work. Everything they produce passes through core gates (`proposal_value_gate`, `policy`, `approval`) before it means anything. |

The uncomfortable cases, decided explicitly:

- `core/loop.py` — **core, but overloaded.** It holds the sequence (core) and a
  large amount of orchestration and formatting (not core). It is the one file
  where the boundary is currently blurred.
- `core/planner.py` — **supplier, not decider.** Its output is untrusted input.
- `core/self_apply_lane.py` / `self_apply_bridge.py` — **runtime**, but they may
  only run behind core gates (approval, gateway, kill switch).

---

## 5. Inputs, outputs, and state

**Inputs.** The operator's question and flags; the file hint; memory blocks
(working, persistent, experience); tool results; the ledgers (model usage,
budget, approvals); configuration (policy lists, budget limits, feature flags).

**Outputs.** An answer; a sequence of decisions with reasons; journal events;
checkpoints; permitted memory writes; approval requests.

**State — and the rule that governs it.** Three lifetimes, and mixing them is
the classic bug:

| Lifetime | Examples | Rule |
|---|---|---|
| **Per run** | `RunContext` (`run_id`, `task_id`), `StepRepetitionTracker`, `TerminationGuard`, `BudgetGovernor.used`, replan history | Must not live on the long-lived agent object. `core/run_context.py` documents why: `api/server.py` shares one `AgentLoop` across requests, so a field would be clobbered by overlapping runs and leak into the next one. |
| **Per session** | working memory, `TraceLogger.trace_id` | Survives turns, dies with the process. |
| **Durable** | persistent memory, ledgers, checkpoints, receipts | Survives restarts; every write passes a core policy. |

**How to prove it:** a test that runs two cycles on one `AgentLoop` instance and
asserts no per-run counter carries over — and one that raises mid-cycle and
asserts the next run starts clean.

---

## 6. How the core actually decides — the gate stack as it runs

This is the observable order in `core/loop.py`. Each line names the journal
event, which is what makes the sequence auditable rather than a claim.

| # | Gate | Model? | Journal event |
|---|---|---|---|
| 1 | Classify the input itself (secrets before the model ever sees them) | no | `data_classified`, `secret_detected` |
| 2 | Clarification gate — is the request answerable as asked? | no | `clarification_gate`, `clarification_request` |
| 3 | Operational domain — is this in scope at all? | no | `out_of_domain` |
| 4 | Referent resolution / local critique | no | `referent_decision`, `local_critique_path` |
| 5 | Memory retrieval (working, persistent, experience) | no | `memory_inject`, `memory_cache_hit` |
| 6 | **Episodic fast path** — a near-identical, high-quality, tool-free past answer is replayed, skipping *both* LLM calls | no | `episodic_fast_path` |
| 7 | Planner call — cheap path and planner cache first | **yes** | `planner`, `planner_cache_hit`, `planner_cheap_path` |
| 8 | Plan admission — parse failures and unknown tools are dropped | no | `plan_parse_failed`, `plan_tool_drop`, `plan` |
| 9 | Reasoning ↔ action consistency | no | `reasoning_action_mismatch` |
| 10 | Per action: policy → gateway → approval | no | `policy`, `gateway_decision`, `approval_request`, `approval_decision` |
| 11 | Execute; count repeats; register compensation | no | `tool_call`, `tool_result`, `step_repetition_detected`, `compensation_registered` |
| 12 | Injection guard on tool output | no | `injection_*` |
| 13 | Evidence collection and trimming to budget | no | `evidence_collected`, `evidence_budget_trim` |
| 14 | Verify the draft against the evidence chain | partly | `verify`, `verification`, `verifier_failure` |
| 15 | Evidence support, confidence vector, subsystem disagreement | no | `evidence_support`, `confidence_vector`, `subsystem_disagreement` |
| 16 | Replan decision under a capped policy | no | `replan`, `replan_attempt`, `replan_exhausted`, `verify_replan_capped` |
| 17 | Stagnation and premature-completion checks | no | `stagnation_detected`, `premature_completion_risk` |
| 18 | Synthesis under the output contract | **yes** | `respond`, `output_contract_violation`, `output_policy`, `answer_enforcement`, `response_composed` |
| 19 | Durable-write permission | no | `memory_write`, `knowledge_pipeline_skipped` |
| 20 | Budget exhaustion → resumable pause | no | `resumable_checkpoint_paused` |

Two of twenty gates use the model. That ratio is the answer to "how does the
core stop the LLM from running everything".

---

## 7. Where the LLM is allowed — and where it must never be

**Allowed** (roles routed through `core/model_router.py`): planning,
synthesis, verification assistance, repair proposals, intent understanding,
subagents. All of them *propose*; none of them *permits*.

**Forbidden — these must stay deterministic**, and today they are:

| Zone | Module | Why it must not use the model |
|---|---|---|
| Operator routing | `strategy_router` | Its own docstring: pure function, no I/O, no LLM. A model here would make "should we call the model?" cost a model call. |
| Action risk | `policy` | Permission must be reproducible and reviewable; a model's mood cannot decide whether a file may be overwritten. |
| Cost and tier | `deep_escalation` | *"free text can never decide whether to spend money on a model"* — the module says so, and enforces it: only two structured reasons unlock the deep tier. |
| Loop control | `replan`, `termination_guard`, `step_repetition` | A stuck model must not be the judge of whether the model is stuck. |
| Durable writes | `memory_policy` | A hallucination that can write itself into memory becomes a permanent fact. |
| Approval | `approval` | The human is the authority; asking the model whether to ask the human defeats the gate. |

**The rule, stated once:** *the model may propose anything; it may permit
nothing, spend nothing, and remember nothing on its own.*

---

## 8. The teeth — what exists, and whether it bites

### 8.1 Routing without the model — **ENFORCING**

1. *Plain:* when the question is one the system can answer from its own state
   ("what's my budget", "what should I do next"), it answers without paying for
   a model call.
2. *Core:* it is decision D1 — the cheapest possible decision, taken first.
3. *Here:* `classify_operator_strategy` maps text to one of 23 local strategies
   or to `general_question`, the only branch that reaches the planner. Plus two
   more model-avoiding paths: the **episodic fast path** (replays a stored
   answer at Jaccard ≥ 0.85 and quality ≥ 0.70, and only for tool-free
   episodes), and the **planner cache / cheap path**.
   *Proof:* `tests/test_operator_intent.py`, `tests/test_strategy_router.py`;
   the fast path's guards are in `core/loop_gates.py:123-189` (см. пометку про прежний якорь выше).

> **RECALL CORRECTION (2026-07-26) — this is the tooth that misses, and it misses
> most of the time.** Precision is fine: when the router matches, it routes
> correctly. Recall is not. Measured over the pattern file's own trigger phrases:
> **62 natural Russian phrases of three or more words route on their own; insert
> ONE neutral word (`уже` / `сейчас` / `теперь`) at a plausible position and 283
> of 372 variants — 76% — stop being recognised** and go to the LLM instead.
> Six intents lose **every** variant: `autonomy_readiness`, `best_next_action`,
> `safe_self_check`, `source_review_plan`, `next_actions`, `project_health`.
> Real examples: `«где слабое место»` routes, `«где сейчас слабое место»` does
> not; `«готов к автономной работе»` routes, `«готов уже к автономной работе»`
> does not.
>
> **Root cause — one shared primitive, not 31 separate bugs:**
> ```python
> def _has_any(text: str, terms: tuple[str, ...]) -> bool:
>     return any(term in text for term in terms)   # literal substring
> ```
> `core/operator_intent_patterns.py` calls it **58 times from 31 matcher
> functions**, against only **5** compiled regexes (added 2026-07-26 for the
> capability family alone, PR #170). Any word wedged between two words of a
> trigger phrase defeats the match.
>
> **Consequence, observed live:** the operator asked «что ты уже умеешь». The
> question fell through to the planner, which answered as a *generic language
> model* — "I can translate texts" — about an agent that has 140 commands, budget
> guards, self-repair and subagents. Cost: 2 model calls, ~9k tokens, 15 cost
> units, verifier confidence **0.008**. The same question phrased as
> «проанализируй сам себя: какие у тебя есть инструменты» produced **8 verified
> chunks out of 8**. The capability existed; the door to it was closed.
>
> PR #170 fixed one of the 31 families. The other 30 remain, and patching them
> one phrase at a time is the wrong shape of fix — see section 10, finding 7.

> **RESOLVED (2026-07-26, PR #172).** Fixed at the primitive, not per family.
> `_has_any_loose` in `core/operator_intent_patterns.py` tries, for a multi-word
> trigger phrase, one regex per gap that allows exactly **one** extra token and
> stays strict everywhere else, so the tolerance cannot compound across a long
> phrase; single-word terms get nothing, because there is no gap to be tolerant
> about. Applied to the 50 `_has_any` calls inside the 24 positive `_matches_*`
> matchers. The 7 calls inside the `_looks_like_*` suppression guards stay
> **strict on purpose** — widening what is recognised is one decision, widening
> what is *blocked* is another and riskier one — and both halves are asserted at
> source level so neither can silently revert.
>
> Re-measured on the same corpus: **0 of 372 variants lost** (was 283). Recall
> was not bought with over-capture, and that was measured too: the repo's own 19
> must-not-route cases (including «проверь свои возможности по документации» and
> «расскажи по README, что ты умеешь», which must reach the LLM) — 0 violations;
> 25 further ordinary turns — 0 captured; two adversarial sentences where both
> halves of a trigger phrase sit far apart — not captured, and they catch a
> mutation that widens the tolerance to two insertions.
>
> The 5 special-case regexes from PR #170 stay: they cover inflection and the
> polite form («что вы уже умеете»), which insertion tolerance cannot reach —
> verified by gutting them and re-measuring (8/10 instead of 10/10).
> *Proof:* `tests/test_local_routing_recall.py` (38 tests), whose corpus is
> derived **from the pattern file**, so a trigger phrase added later is covered
> without anyone remembering to extend a list.
>
> What this does **not** fix: recall for phrasings that are not near-variants of
> a listed trigger phrase. The router is still a phrase list; it is now a phrase
> list that survives an inserted word.

### 8.2 Call budgets — **ENFORCING, with one default worth a decision**

1. *Plain:* a run may spend only so many cycles, calls and tokens before it
   must stop.
2. *Core:* D9. Cost is a first-class decision, not an accident of how long a
   loop ran.
3. *Here:* four layers — `BudgetGovernor` (cycles, agent runs, test runs,
   approval requests, proposals), `ModelUsageLedger` raising
   `ModelBudgetExceeded` mid-run, `budget_kill_switch` as a hard stop, and
   `evidence_budget` trimming context. Exhaustion is *not* a crash: it becomes
   a paused checkpoint that `--resume` picks up (`app/budget_guard.py`).
   *Proof:* `tests/test_budget_governor.py`, `test_budget_ledger.py`,
   `test_budget_kill_switch.py`, `test_budget_resume.py`, `test_evidence_budget.py`.
   **Finding:** `BudgetLimits` defaults `max_llm_calls=0`, which means
   *unlimited* — the counter is tracked and never denied. `CampaignConfig` sets
   100, so campaigns are capped, but the plain autonomous path is not. That is a
   deliberate-looking default that deserves an explicit decision (§12).

### 8.3 Escalation rules — **ENFORCING**

1. *Plain:* the expensive model opens only when the operator says why, in words
   the system recognises.
2. *Core:* D5.
3. *Here:* `evaluate_deep_escalation` — five checks, each failure producing a
   *downgrade* rather than an error, so the work still completes on the cheaper
   model. The agent cannot construct the escalation object for itself: the
   autonomous path never builds one, so it always downgrades.
   *Proof:* `tests/test_deep_escalation.py`.

### 8.4 Structured output validation — **ENFORCING**

1. *Plain:* the model's answer is parsed and checked, not trusted.
2. *Core:* D3 — an unparsable or tool-inventing plan must never reach execution.
3. *Here:* plan parsing with `plan_parse_failed`; unknown tools dropped with
   `plan_tool_drop`; the Output Contract enforced at synthesis
   (`output_contract_violation`, `core/output_policy.py`); citations checked
   against the evidence chain.
   *Proof:* `tests/test_output_policy.py`, `test_output_policy_integration.py`.

### 8.5 Preconditions and postconditions — **MIXED**

1. *Plain:* before acting, check it is allowed; after acting, check it worked.
2. *Core:* D4 (pre) and D6 (post).
3. *Here:* preconditions are strong — `PolicyGate` classifies every action by
   risk *including its arguments* (`file_write` is reversible for a new file,
   irreversible for an overwrite), the gateway adds kill-switch and readiness
   stops. Postconditions are weaker: the verifier reports, and `completion_marker`
   exists, but nothing *blocks* on the completion claim. Since 2026-07-27 the
   claim is at least examined structurally — `core/completion_obligation.py`
   asks whether the cycle incurred a duty to observe or run something and left
   it unmet **without saying so**, from the object the question names, the
   admitted plan steps and `realtime_required` rather than from wording. It
   emits `completion_obligation` ~~and remains observational (8.11)~~ —
   **corrected 2026-08-02 (PRs #216/#227, operator's ruling):** observational
   *mid-run* only. At banking the verdict now has authority: the sensor's
   finding is stored with the episode (`EpisodeRecord.defect_signals`), and an
   unmet obligation on a run that declared `achieved` lowers the completion
   verdict to `partially_achieved` (`completion_override` records the
   displacing fact; the declaration itself is never edited) and thereby
   withholds procedure credit. Nothing stops or replans mid-run — the run's
   path is unchanged; what changed is the verdict it is banked under.

### 8.6 State control — **ENFORCING**

1. *Plain:* the agent always knows which run it is in, and can be resumed
   exactly where it stopped.
2. *Core:* D9 plus the integrity of every other decision.
3. *Here:* `RunContext` as a `ContextVar` (not a field — see §5),
   append-only checkpoints per trace, `state_integrity` checks.
   *Proof:* `tests/test_checkpoint.py`, `test_state_integrity.py`,
   `test_budget_resume.py`.

### 8.7 Repeat detection — **OBSERVING**

1. *Plain:* notice when the agent is doing the same thing over and over.
2. *Core:* D7.
3. *Here:* `StepRepetitionTracker` fires at the third identical
   `(tool, arguments)` pair; `TerminationGuard.observe_attempt` fires when two
   consecutive replans produce the same failure signature. Both emit an event
   (`step_repetition_detected`, `stagnation_detected`) and **change nothing** —
   the loop logs the stagnation and proceeds to the ordinary replan decision.
   The remaining attempt budget is still spent on the same broken plan.
   Since 2026-07-27 stagnation additionally gets **shadow accounting**: at the
   end of the cycle the loop emits `stagnation_shadow` with `would_stop`,
   `would_save_attempts` and `would_change_result` — the last answered honestly,
   by whether any artifact actually arrived after the detection point. It stops
   nothing; it makes the cost of stopping measurable before anyone decides to.
   *Proof of the current behaviour:* `tests/test_step_repetition.py`,
   `tests/test_termination_guard.py`, `tests/test_sensor_shadow_scenarios.py`.

### 8.8 Autonomy limits — **ENFORCING**

1. *Plain:* an unattended run is allowed less than a supervised one.
2. *Core:* D4 and D9 together.
3. *Here:* `PolicyGate.blocked_tools` removes effectful tools during dry runs;
   `escalate_reversible_tools` promotes even a safe new-file write to "ask the
   human" on agent-driven paths; the unattended memory profile forbids replaying
   stored answers; `BudgetGovernor` caps cycles.

### 8.9 Mandatory approval for dangerous actions — **ENFORCING**

1. *Plain:* irreversible or outward-facing actions stop and wait for a human.
2. *Core:* D4.
3. *Here:* `escalate` from `PolicyGate` → `ActuationGateway` → `ApprovalProvider`
   → approval inbox, with receipts. In one-shot mode with no provider wired, the
   default is refusal, not silent execution (`cli/one_shot.py`).
   *Proof:* `tests/test_approval.py`, `test_actuation_gateway.py`,
   `test_gateway_escalate_contract.py`, `test_approval_receipts.py`.

### 8.10 Fallback under uncertainty — **MIXED**

1. *Plain:* when unsure, ask, degrade or say so — do not guess confidently.
2. *Core:* D2 and D7.
3. *Here:* ENFORCING — the clarification gate returns a question instead of an
   answer; deep escalation downgrades instead of failing; `synth_resilience`
   retries synthesis; `low_evidence_policy` truncates unsupported output.
   OBSERVING — low confidence produces an event, not a retreat (8.11).

> **Two of those had been cancelling each other (measured and fixed 2026-07-27).**
> The B-1 clarification gate prepends its questions when the loop is stuck, and
> `low_evidence_policy` rebuilds the answer when the evidence is thin. Both fire
> on the same turn — a stuck loop that still produced a long unsupported answer
> is the expected shape, not an exotic one — and the rebuild deleted the
> questions, because both deciders were writing to one string and the truncation
> ran later. Reproduced through the real loop in the default configuration:
> `output_policy applied=True`, `clarification_gate` with three questions,
> `answer_enforcement applied=True`, and none of the four strings in the 426
> characters that shipped.
>
> The cycle now composes the response through `core/response_draft.py`, which
> separates **claims** (which truncation may delete — that is its purpose) from
> **notices** (prose *about* the answer, which no verdict on the evidence can
> make untrue). Composition happens once, in `render()`, and the journal event
> `response_composed` reports every contribution and anything that failed to
> survive. This is the first place in the cycle where two deciders are
> *reconciled* rather than merely ordered. See MIR-063.

### 8.11 Independent evaluation of the result — **OBSERVING (the main gap)**

1. *Plain:* something other than the author checks whether the answer is
   supported.
2. *Core:* D6.
3. *Here:* the machinery is built and running — `Verifier` labels every claim
   chunk, `core/evidence_support.py` turns that into an **applicability-aware**
   report (does this turn owe evidence at all; if so, what fraction of the
   claims is backed; is any citation fabricated), `confidence_vector` decomposes
   the verdict into three axes, `subsystem_disagreement` finds contradictions
   between subsystems. **All of those write to the journal and change nothing.**
   `core/evidence_support.py` says so in its own docstring: *"Still
   observational."* The weak-support threshold (0.45) survives only to keep
   historical log comparisons meaningful — it gates a boolean in telemetry,
   never behaviour.

   The layer that *does* change the answer is separate and enforcing:
   `core/low_evidence_policy.py` + `core/unsupported_claims.py`. Reading
   `evidence_support` as the thing that could "block" an answer is the mistake
   the notes below record and correct.

> **MEASURED AND ACTED ON (2026-07-27).** "Parameterised for the day the switch
> is flipped" turned out to be the wrong frame for this sensor: the problem was
> never the threshold. Measurement
> ([audit/SENSOR_SIGNAL_MEASUREMENT.md](audit/SENSOR_SIGNAL_MEASUREMENT.md))
> showed the scalar gave almost the same zero to three different situations —
> evidence never required, evidence required and absent, and citations that
> resolve to nothing — and that **11 of its 12 firings on real traffic were the
> first case**, on 6 of which the enforcing layer had already recorded
> `no_evidence_expected` for the same turn.
>
> Operator ruling: **do not connect it to replan** (measured cost: +86% model
> calls to re-run mostly honest general-knowledge answers, and a signal present
> on 86% of turns separates almost nothing), and **redefine what it measures**.
> `core/confidence_gate.py` is now `core/evidence_support.py`: <!-- historical-ref: the rename itself --> it reports
> `applicable=False, score=None` when no evidence was owed, a real `0.0` when it
> was owed and missing, and a separate `citation_integrity_violation` flag when
> the answer cited sources that resolve to nothing. Applicability is decided by
> the same `is_evidence_expected` the enforcing layer uses, so observer and
> enforcer can no longer disagree about whether this turn owed evidence.
> The journal event is `evidence_support`, emitted on every verified turn.
> It remains **observational**.

> **CORRECTION (2026-07-26).** The sentence above — "the gate is the main gap" —
> was measured and is **wrong in its diagnosis**. An enforcement layer already
> exists and is already wired into the loop: `core/low_evidence_policy.py` (its
> own docstring calls itself *"the enforcement layer paired with the gate"*) plus
> `core/unsupported_claims.py::apply_answer_enforcement`, called at
> `core/loop_response_deciders.py:286` (жил в `core/loop.py` до разбора на
> модули; прежний якорь на строку 2170 к тому времени уже указывал не туда и
> держался только тем, что попадал в диапазон файла). It rewrites a severely
> under-supported answer into a short
> honest reply and downgrades the Confidence line.
>
> Three separate reasons it changed nothing in practice, all measured by replaying
> the real functions against 8 recorded answers in `logs/` (the replay reproduced
> the logged verdicts 8 for 8):
>
> 1. ~~**It runs in `mode=off` by default.** `enforce_unsupported_claims_mode()`
>    reads env `AGENT_ENFORCE_UNSUPPORTED_CLAIMS`; unset → `off`, so `applied`
>    can never become true.~~ **WRONG — corrected below; the flag governs a
>    different, narrower path than this said.**
> 2. **The 8-chunk floor.** `_DEFAULT_MIN_TOTAL = 8`; every real firing had 3-6
>    chunks → `reason=too_few_chunks_to_truncate`.
> 3. **`no_evidence_expected`.** For an empty evidence chain with no realtime
>    intent — or any role in `_GENERATIVE_ROLES` — the gate is skipped *by
>    design*, correctly: a pure reasoning answer has nothing to cite.
>
> Measured outcome of each option, on those 8 answers: flipping `mode=on` alone
> changes **0**; `on` plus a floor of 3 *only when `verified == 0`* changes **3**.
> Turning the gate itself into a blocker — the fix this document originally
> implied — would have changed **nothing**, because the gate is not where the
> decision lives. See [[fix at the decider, not the observer]].

> **CORRECTION TO THE CORRECTION (2026-07-26).** Reason 1 above was wrong about
> what the flag controls, and the error mattered: it described the enforcement
> layer as fully inert, when its most consequential path ships on every run.
> `apply_answer_enforcement` (`core/unsupported_claims.py`) has **two** paths and
> the flag governs only one of them.
>
> | path | what it does | gated by the flag? |
> |---|---|---|
> | `insufficient_evidence` — long-answer truncation | rebuilds a severely under-supported answer into a short honest stub | **No. Always on.** It returns `applied=True` regardless of mode, and `core/loop.py` writes that answer back. |
> | `unsupported_world_claims` — short categorical hedge | downgrades `Confidence` and adds an `Unverified:` note for `< 8`-chunk answers carrying categorical world claims | **Yes** — `applied = mode == "on"`; `shadow` logs `would_change_answer` only. |
> | `verifier_failure` / `malformed_report` soft-fail | *keeping* the draft is unconditional; only the explanatory note appended to it is flag-gated | note only |
> | `local_critique_preserved` | invariant, never flag-gated | no |
>
> So `AGENT_ENFORCE_UNSUPPORTED_CLAIMS` is the rollout switch for the **new,
> claim-level short path** (critique plan PR3), not a master switch for
> enforcement. `off` is the default and means: the pre-PR3 long truncation is
> live, the new short-answer hedge is not.
>
> The measurement above is still correct and now reads correctly too: flipping
> `mode=on` changed 0 of those 8 answers because none of them reached the short
> categorical path — not because enforcement was switched off.
>
> **Proof it is live:** issue #119 is a recorded production turn with the flag
> unset in which `low_evidence_truncation` fired and suppressed 1287 characters
> of the agent's own self-analysis. An inert layer cannot do that.
>
> **What changed since (issue #119).** The truncation gate now decides on
> `(verified + dialogue_supported) / total`. `core/evidence_classes.py` splits
> the evidence the system holds into `external_world`, `session_dialogue`,
> `trace`, `self_analysis` and `generative`, and the verifier gives a claim about
> *this session's own exchange* its own verdict, `dialogue_supported` — supported
> by the transcript, never counted as `verified`, and never available to a claim
> about the outside world. Unsupported world claims are filtered exactly as
> before; what stopped being deleted is the agent explaining its own mistake.
> *Proof:* `tests/test_self_analysis_evidence.py`.

### 8.12 The decision journal — **ENFORCING**

1. *Plain:* every decision is written down with its reason, so it can be
   reviewed later.
2. *Core:* D10 — without it none of the above can be audited.
3. *Here:* 71 distinct event kinds across 100 call sites in `core/loop.py`
   alone, plus tool receipts, checkpoints, the model-usage ledger with integrity
   hashes, and the incident log. Decisions carry structured reasons
   (`PolicyDecision.reasons`, `DeepEscalationDecision.route_reason`,
   `BudgetDecision.reason`), not free text.

---

## 9. What the cognitive core does not do

- It does not **store** knowledge; it decides what may be stored.
- It does not **execute** tools; it decides whether a tool may run.
- It does not **schedule** work; it decides what one cycle concludes.
- It does not **talk** to the operator; the CLI does.
- It does not **generate**; the planner and synthesizer do, and their output is
  input to the core, not authority over it.
- It does not **improve itself** unattended; self-build and self-apply are
  producers behind core gates.

---

## 10. What this audit found

1. **The cognitive core already exists** — roughly thirty deterministic
   deciders, a twenty-gate sequence, and a 71-event journal. This was never a
   greenfield question. The work is to *declare* the boundary, not to invent it.
2. ~~**The boundary is real but unguarded.**~~ **GUARDED (2026-07-26).** The
   claim that `core/` imported nothing upward was also not quite true when it was
   written: `core/campaign_io.py` reached into `agent_tick._read_heartbeat` and
   two sibling private helpers. Those moved to `core/heartbeat_io.py` — whether
   the daemon is alive is an *input to a decision*, so reading it belongs in the
   core; the tick script still owns when to write it. The boundary is now
   enforced by `scripts/architecture_invariants.py` (INV-1), together with three
   further invariants the architecture rests on: no orphaned deciders (INV-2 —
   the `recover_stuck` anti-pattern), documented env flags exist (INV-3), and
   every verifier verdict is bucketed by its consumers (INV-4). Each check is
   itself tested against a planted violation, so it cannot decay into a no-op:
   `tests/test_architecture_invariants.py`.
3. **Five sensors do not bite.** Confidence gate, stagnation, premature
   completion, reasoning↔action mismatch and subsystem disagreement all detect
   and log. ~~The most consequential is the confidence gate: an answer can be
   fully unverified and still ship, with an event nobody reads.~~ **Corrected
   2026-07-26 (see §8.11):** the answer *can* still ship unverified, but the gate
   is not the reason. Enforcement exists and is wired. ~~it is inert because
   `AGENT_ENFORCE_UNSUPPORTED_CLAIMS` defaults to `off`~~ — **corrected again the
   same day:** the long-answer truncation path is **always on** (issue #119 is a
   production turn where it fired with the flag unset); the flag gates only the
   new claim-level short path. What kept it quiet on the 8 recorded answers is
   the 8-chunk floor and the by-design skip for pure-reasoning turns. Measured:
   flipping the mode alone would change 0 of 8, because none of them reached the
   path the mode controls.

   **Still true after 2026-07-27, but two of the five were rebuilt rather than
   connected** (PR #177; measurement in
   [audit/SENSOR_SIGNAL_MEASUREMENT.md](audit/SENSOR_SIGNAL_MEASUREMENT.md)).
   ~~None of the five enforces anything today.~~ **Corrected 2026-08-02:**
   four of the five still enforce nothing; S3 became the exception *at
   banking time* under the operator's ruling (see the row below and §8.5).
   What changed in #177 is *what they say*:

   | sensor | current module / event | current state |
   |---|---|---|
   | S1 evidence support | `core/evidence_support.py` → `evidence_support` | redefined: applicability, support score, weak-support flag, citation-integrity flag. Not a confidence gate, not connected to replan. |
   | S2 stagnation | `core/termination_guard.py` → `stagnation_detected` + `stagnation_shadow` | shadow accounting only; stops nothing. |
   | S3 unmet obligation | `core/completion_obligation.py` → `completion_obligation` | detector replaced; the keyword detector survives only as `shadow_keyword_detector` inside the new event. **2026-08-02: authoritative at banking** — lowers a declared `achieved` to `partially_achieved` and withholds procedure credit (PR #216); still decides nothing mid-run. |
   | S4 reasoning ↔ action | `core/reasoning_action_check.py` → `reasoning_action_mismatch` | unchanged by decision; still an observer — but since 2026-08-02 its firing is *banked* with the episode (`defect_signals`), so a repeated fault is visible in memory. Recording grants no power (pinned by test). |
   | S5 subsystem disagreement | `core/subsystem_disagreement.py` → `subsystem_disagreement` + `subsystem_disagreement_shadow` | shadow accounting only; replans and escalates nothing. |

4. **One budget is off by default.** `BudgetLimits.max_llm_calls = 0` means
   unlimited on the plain autonomous path.
5. **`core/loop.py` is where the boundary blurs** — ~~3882~~ 4047 lines mixing
   the decision sequence with orchestration and formatting. ~~It is the natural
   next extraction target, and it should be done the way `main.py` was:
   characterize first, move in small proven steps.~~ **Done 2026-08-02, exactly
   that way** (#217, #218, #219, #221, #222, #224): six bounded pieces moved to
   their real homes with AST-closure checks, byte-verbatim bodies and oracle
   tests — −686 lines. What remains is genuine loop orchestration
   (`_run_inner`, `_execute_step`, `_synthesize`); carving *those* is an
   architectural change, registered as out of conveyor scope, not a pending
   extraction.
6. **Deep escalation covers only the `for_task` path.** Roles routed through
   `for_role` (verifier, repair, subagent) cannot reach the deep tier at all —
   which is safe, and documented, but means the gate is narrower than it looks.
7. **The most expensive finding is a recall failure, and it is one line of code**
   (added 2026-07-26; see §8.1 for the measurement). `_has_any` matches trigger
   phrases as literal substrings and is called 58 times from 31 matchers, so one
   word inserted into a phrase sends the turn to the LLM: **76% of natural
   phrasings miss**. Six intents lose every variant. The agent then answers
   *about a generic language model instead of about itself* — the operator saw
   exactly that, and the system scored its own answer 0.008.

   Why this is a core defect and not a wording annoyance: D1 ("do we need the
   model at all?") is the cheapest and first decision in the whole sequence, and
   it is the one gate whose failure *costs money and produces a wrong answer at
   the same time*. Everything downstream then behaves correctly — the verifier
   flags the answer, the confidence vector scores it 0.8% — and none of it helps,
   because the question was already in the wrong lane.

   **Shape of the fix.** Not 30 more phrase lists: one tolerant matching
   primitive, with the loosening measured in both directions. Widening recall
   without measuring over-capture is how a router starts hijacking ordinary
   questions into operator commands — the tolerance for the capability family had
   to be cut from two inserted words to one for exactly that reason, and the test
   that caught it was a deliberate over-capture case, not a passing suite.
8. **No document in this repository measured recall before this pass** — not this
   one, not `LIVE_PROBE_FINDINGS.md`, not `MASTER_ISSUE_REGISTRY.md`. The
   registry's related entry diagnoses a *missing intent kind*; the measured root
   is a *brittle primitive*, which is why fixing the former would not have
   changed the operator's session at all: the intent kind existed and was still
   missed. An audit that only asks "is this guard correct?" cannot find this
   class of defect.

---

## 11. How to prove each part before writing any new implementation

The precedent is Phase 0 of the `main.py` extraction: freeze current behaviour
in tests *before* touching anything, and make every later change prove itself
against that. Concretely, in this order:

**Step 1 — the boundary becomes a rule (1 test, no production change).**
`test_core_boundary.py`: `core/` may not import from `cli/`, `app/`, `main`, or
`tools/` implementations. Passes today; from then on a violation is a red test
rather than a silent architectural change.

**Step 2 — the decision journal becomes a contract.**
`test_decision_journal_contract.py`: for each of the ten decisions in §3, run a
scenario and assert the named event appears **with a non-empty reason**. This
freezes the vocabulary so a refactor cannot quietly stop recording a decision.

**Step 3 — an LLM-free proof.**
`test_no_model_calls_for_local_strategies.py`: replace the model client with one
that raises on any call; drive every local strategy and the episodic fast path;
assert answers are still produced. Today's claim "two of twenty gates use the
model" becomes a test.

**Step 3b — a RECALL proof, per intent (added 2026-07-26; this is the step whose
absence hid finding 7).**
`test_local_routing_recall.py`: for every local strategy, take the trigger
phrases the pattern file itself declares, generate the variants a human actually
types — one inserted adverb, the polite form, a different word order — and assert
they still route to the same intent. Then the mirror half, in the same file: a
corpus of ordinary questions that must **not** route, so widening recall cannot
be paid for with over-capture. A bite test proves a guard is right when it fires;
only this proves it fires at all.

**Step 4 — a bite test per tooth.**
For every ENFORCING mechanism, a scenario where removing it changes the outcome
— proven the way we proved the patch seams: break it deliberately, watch the
test fail, restore it. For every OBSERVING mechanism, a test that asserts
*today's* log-only behaviour, so that turning it into a block is a deliberate,
reviewable change and never an accident.

**Step 5 — cost and termination proofs.**
Budget exhaustion produces a resumable pause, not a crash (extend
`test_budget_resume.py` to the runtime path); the replan cap holds under a
planner that always fails; the kill switch stops a daemon cycle before an agent
is built (already covered by `test_budget_kill_switch.py` — extend to the
gateway path).

**Step 6 — only then** decide whether the core becomes a package. Moving thirty
modules into `cognitive_core/` buys nothing that steps 1–5 have not already
bought, and it repeats the risk profile of the `main.py` extraction. If the
boundary is guarded and the journal is a contract, the folder is a cosmetic
follow-up that can be done — or not — at leisure.

---

## 12. Decisions the owner has to make

**Re-ordered 2026-07-26 by measured damage.** The list below used to open with the
confidence gate; measurement moved it down and put a recall failure on top.

**Settled on 2026-07-27 (PR #177), so no longer on this list:** whether S1
becomes a replan trigger (**no** — it is evidence-support telemetry now,
`core/evidence_support.py`), and whether S3's keyword list is worth repairing
(**no** — replaced by `core/completion_obligation.py`). S2 and S5 were not
decided; they were given shadow accounting so that deciding them later rests on
numbers. The addendum in
[audit/SENSOR_SIGNAL_MEASUREMENT.md](audit/SENSOR_SIGNAL_MEASUREMENT.md) records
the ruling per sensor.

1. ~~**Fix `_has_any` recall at the primitive, or keep patching phrase lists?**
   76% of natural phrasings miss (§8.1, finding 7).~~ **DECIDED and DONE
   (2026-07-26, PR #172): at the primitive.** `_has_any_loose` tolerates one
   inserted word per gap in the positive matchers, and nothing in the
   suppression guards. Re-measured 0 of 372 variants lost, with no over-capture
   on the repo's 19 must-not-route cases. See §8.1.
2. **Should a worthless answer be banked as a success?** A run the system itself
   scored `overall_confidence = 0.008` was written to episodic memory as
   `outcome=success`. Unlike the others this one **compounds**: later runs read
   that memory as experience, so the agent is learning that unsupported
   self-praise is good work.
3. ~~**Should `_GENERATIVE_ROLES` exempt factual questions?**~~ **Answered no
   (2026-08-01, `f32d602`).** Measured: "how many TODO/FIXME are in `core/`?"
   ran under `role=programmer`, so evidence checking was skipped entirely for a
   counting question. Counting is not code generation. Confirmed a second time
   live — "what does `core/model_router.py` do?" read the file, produced 7
   chunks of which 6 verified, and still logged `no_evidence_expected`. The
   exemption keyed on WHO answered, not WHAT was produced. It now also requires
   the answer to carry a generated artifact (fenced block or unified-diff
   header), which is exactly as far as its own rationale reaches: generated text
   can never appear verbatim in its source file. Same question after the fix:
   `applicable=True, score=0.821, reason=measured`.
4. **`AGENT_ENFORCE_UNSUPPORTED_CLAIMS=on`, and then the truncation floor?**
   Arming the mode is free — measured 0 of 8 answers change. Note what the
   question is *not* asking (§8.11 correction): the long-answer truncation is
   already live and unaffected by the mode; what would be armed is the
   claim-level short-answer hedge. The floor is the real decision (3 of 8 change
   with a floor of 3 when nothing is verified), and it should wait for more than
   8 recorded answers.
5. **Should stagnation stop the run?** Two identical failure signatures still
   cost the full remaining attempt budget. Open on purpose, but no longer
   unmeasurable: `stagnation_shadow` now records `would_stop`,
   `would_save_attempts` and `would_change_result` per run (§8.7), so the answer
   can come from traffic instead of from intuition. Same for S5 via
   `subsystem_disagreement_shadow`.
6. **Should `max_llm_calls` have a real default** instead of unlimited?
7. **Is `core/loop.py` the next extraction?** It is the last place where the
   core's sequence is entangled with orchestration.

_Source of facts: `core/` (134 modules), `app/`, `cli/`, `tools/`, `api/` read at
`main` @ `eec6507` on 2026-07-26; module docstrings quoted verbatim where they
state a limitation. The 2026-07-26 amendments add: a recall measurement over the
pattern file's own 62 natural trigger phrases (372 variants), a replay of the real
enforcement functions against the 8 answers recorded in `logs/` (reproducing the
logged verdicts 8 for 8), and one live operator session._

_**Mixed provenance, stated rather than hidden.** The audit above was read at
`eec6507`; the present-tense sections listed in the header note were re-read and
corrected at `main` @ `3128d4c` (the PR #177 merge) when the sensor work landed.
So a dated finding here is provenance from `eec6507`, while every statement
about what the code does **today** is grounded at `3128d4c`. Where the two would
have contradicted each other, the later reading wins and says so at the spot._
