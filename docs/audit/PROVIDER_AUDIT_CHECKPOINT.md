# Where the cognitive-memory work stands — state page

> **What this file is.** One page that answers *"where are we, and what was
> decided?"* for the cognitive-memory / structured-outputs work. It exists
> because the doc set grew by accumulation (see `INDEX.md` §1) and the thing
> that keeps getting lost is not a document — it is the **reasoning** that made
> the fixes take their shape.
>
> **What this file is NOT.** Not a register. It owns **no** issue status (that
> is `MASTER_ISSUE_REGISTRY.md`), **no** measured provider facts (those are
> `PROVIDER_STRUCTURED_OUTPUT_AUDIT.md`), and **no** target design (that is
> `MEMORY_LIFECYCLE_CONTRACT.md`). Where it restates anything, the owner wins.
>
> **Filename is historical.** This file began on 2026-07-21 as
> `PROVIDER_AUDIT_CHECKPOINT.md` — a work-state record for the structured-outputs
> spike, written with an explicit disposal rule: *"once the addendum lands and
> the reading gate is cleared, this file's content is either absorbed or
> deleted; it must not become a fourth place where the same question has an
> answer."* Both conditions were met the same day, so it was rewritten in place
> rather than left to accumulate. The path was kept to avoid an unannounced
> rename; renaming it is available and would cost nothing but a link update.

---

## 1. The thesis — the thing at risk of being lost

> **We are not copying the human brain. We are transferring its functional
> decomposition, measuring where its behaviour is dangerous for an autonomous
> system, and at exactly those points deliberately building better than the
> biological prototype.**

Three consequences that are not optional:

- **Biology supplies the decomposition, never the guarantees.** The
  decomposition has an empirical warrant — lesion dissociations show the memory
  systems really are separable — but human memory is reconstructive, its source
  monitoring is unreliable, and confidence rises with repetition. An engineered
  system must exceed the prototype precisely there.
- **Sub-agents are a *consumer* of the cognitive memory, not its centre and not
  the reason it exists.** Memory-boundary control for children is a derived
  capability of the general system.
- **The human stays the owner of policy.** Merge, kill-switch, approval of
  irreversible actions. This invariant has no biological ancestor and must not
  be given a fabricated one.

## 2. The method — five steps, and the two rules that make it hold

```
биологический компонент
        ↓
его полезная функция
        ↓
его известная слабость
        ↓
инженерный инвариант
        ↓
воспроизводимый тест
```

**Rule for step 3 (the weak link).** "Known weakness" is a claim about
neuroscience, and most such claims are contested or method-dependent. So:
**the invariant must stand on its own engineering grounds independently of the
biology.** Acceptance test for a row — *delete step 3; is the invariant still
justified?* If not, the row is not ready. Practical payoff: a revision in
neuroscience never invalidates the code.

**Rule for step 5 (paired, not single).** One prohibition test degenerates the
invariant into "never trust anything". Every row needs **two**: the forbidden
behaviour fails, and legitimate behaviour keeps passing. The repo already does
this instinctively — see the protective test in §3 below, and MIR-057's closure
criterion #3 ("a complete answer carrying a local disclaimer is still a
completed task; the fix must not buy correctness by declaring everything
incomplete").

**A second track exists.** Some invariants have **no biological ancestor**:
Policy Gate, kill-switch, human merge authority, immutable evidence chain,
operator revocation. They exist because the system is auditable and owned. Do
not invent biology for them — it would weaken their justification.

## 3. One row carried end to end (proof the method executes)

```
Реконсолидация
↓ извлечение активирует и обновляет след
↓ повторение повышает уверенность без новых доказательств
↓ MEMORY_LIFECYCLE_CONTRACT §12 №2 + §7.3 — реплей не считается
  верификацией, не повышает статус источника, не становится
  кандидатом на реплей
↓ tests/test_fast_path_verification_provenance.py
    mir041_4_replay_must_not_be_recorded_verified
    mir041_5_replay_episode_must_not_be_fast_path_candidate
    mir041_6_repeated_asks_must_not_self_reinforce_verified_chain
    mir_protective_7_genuinely_verified_episode_may_fast_path  ← the guard
```

Biology → function → weakness → invariant → executable pair. The method is
demonstrable, not proposed.

## 4. Measured and frozen (owners hold the detail)

Established against code on `mir-057-task-completion-axis`, 2026-07-20/21. These
do not change when the architectural framing changes.

- **The adapter cannot express structured outputs at all** — zero occurrences of
  `response_format` / `json_schema` / `strict`; `core/llm.py` is the only
  provider call site; surface is Chat Completions, not Responses. → audit §1.
- **Live routes — three models, not two** (corrected 2026-07-21 against a real
  session start). Per question: standard → `openai / gpt-5.6-terra`, light →
  `openai / gpt-5.4-nano-2026-03-17`. **Role default → `openai / gpt-5.4-mini`**
  from the custom registry — this is what the session banner prints, and by the
  registry it is also the `verifier` model. → audit §3 correction.
- **Model capability for strict JSON Schema is UNDETERMINED** and may not be
  inferred, recalled or guessed. Only a live probe answers it. → audit §4.
- **Declaration vocabulary is five tokens, not seven** — `cancelled` / `unknown`
  are facts the loop observes, never claims the answer may make. → audit §6.
- **MIR-060 reproduces**: four false derived claims score `verified`; the
  containment check flips on an unrelated `port=12`; zero NLI calls once a
  citation resolves. → registry, MIR-060.
- **The M1 contract does not close MIR-060** — its verification rules are
  entirely trust-class; a `file` evidence is `tool_observation`, inside the
  allowed set. → registry, MIR-060.
- **The contract has no completion dimension.** `completion_state` /
  `declared_completion` (MIR-057) landed a day after it; §11's MIR map does not
  know MIR-057. And the token **`blocked` now means two different things** —
  a terminal `usage_eligibility` state in the contract, a completion state in
  the code. Two normative vocabularies, one word.
- **`внимание` has zero coverage anywhere** — contract, ROADMAP, modules. Only
  budgeting exists (`evidence_budget`, `compactor`). Budget truncates after the
  fact; attention allocates before.
- **Forgetting has a second, ungoverned mechanism** — the episodic store's
  200-record FIFO window. Unmodelled in the contract, and MIR-058 measured it
  destroying the audit evidence for procedural credit.

## 5. Decided

- Structured Outputs are a candidate for the **transport of the declaration** —
  not a replacement for the structural overrides and not a replacement for
  verification.
- Sub-agents are a consumer of the memory system (see §1).
- Adversarial framing and a separate critic are **later, separate** experiments,
  so causal attribution of either effect is not lost. The critic is not designed
  until its input contract is (task, trace, tool results, exit codes, evidence,
  environment state — final answer text alone is insufficient).
- The Voyager `−73%` and `−28–35% ECE` figures may not appear in any
  architectural justification until the primary table, the metric definition and
  applicability to a **categorical** completion model are shown.

## 6. Open — all operator decisions

From the provider audit:
1. Run the live capability probe? (real spend, external requests)
2. Truncation policy on a structured path — disable auto-continuation, or split
   the channels (prose vs. declaration).
3. Enum width — five or seven. *(Recommendation: five. Not confirmed.)*
4. Chat Completions or Responses API.

From the contract reconciliation (block v3's normative half):

5. Completion axis — a seventh envelope dimension, or explicitly outside it?
6. The `blocked` collision — which of the two names changes?
7. FIFO window — governed forgetting, or acknowledged ungoverned mechanic with a plan?
8. Attention — a section in M1, or an explicit "out of scope"?

## 7. Not done

- **No live probe.** No API call to any provider.
- **No code change.** Provider layer, marker path, persistent schema,
  eligibility, credit/debit, parser policy, MIR-057 migration — all untouched.
- **The thesis in §1 is not yet in any governing document.** Proposed home:
  `MEMORY_LIFECYCLE_CONTRACT.md` v3 front matter (Part A biological
  decomposition + Part B engineering deviations), as one version bump — not a
  parallel document. Note: **six of the seven Part-B rows already exist** as
  contract §12 negative invariants; Part B supplies the missing *rationale* for
  rules that are already written.
- **The memory-governance docs are still not readable by the agent itself.**
  `MEMORY_FIX_PLAN` B.3 proposed a thematic manifest
  (`_MEMORY_GOVERNANCE_DOC_PATHS`, mirroring the sub-agent one that exists) so
  the agent can read its own problem history on memory questions. Never done.
  Until it is, this work exists only for the operator.

## 8. Discipline record — unearned claims caught, 2026-07-20/21

Kept because the pattern is the point, not the individual corrections.

| Claim | Why it was withdrawn |
|---|---|
| Voyager `−73%`, ECE `−28–35%` in architectural justification | borrowed effect sizes without primary table, metric definition, or applicability to a categorical model |
| "§5 Capability Discovery is empty; §6 Model Management took its place" | no such architectural substitution was established; the project never mapped model management into §5 |
| "`fallback_if_denied` carries directly from permission negotiation to model capabilities" | authority ≠ availability; permission denial is not unsupported capability |
| "The code reproduced human cognitive biases" | a causal claim from behavioural correspondence alone. Correct form: *some implemented heuristics functionally reproduce known human memory failure modes — familiarity, fluency, repetition-based confidence, one-shot overgeneralisation* |
| Novelty of the method | would require a literature survey covering classical cognitive architectures (SOAR, ACT-R, CLARION, Sigma), complementary-learning-systems work in ML, neuro-symbolic approaches, recent LLM-agent cognitive architectures, and assurance-case methodology. Not performed |

Three of these were caught **inside the same session** by the same discipline.
That is the project's working immunity to unearned claims, and it is worth as
much as any single fix.

## 9. Who owns what

| Question | Owner |
|---|---|
| Any issue's status | `MASTER_ISSUE_REGISTRY.md` |
| What the provider layer can and cannot express | `PROVIDER_STRUCTURED_OUTPUT_AUDIT.md` (+ its dated addendum §A1–A9) |
| The target memory design | `MEMORY_LIFECYCLE_CONTRACT.md` (v2-draft, unapproved, partly overtaken) |
| How memory flows today | `MEMORY_MAP.md` |
| Which document answers which question | `INDEX.md` |
| Where this work stands | this file |

---

*Read-only session product. No code changed. Documents edited are disclosed in
the session record; this file replaced its own predecessor under the disposal
rule quoted in the header.*
