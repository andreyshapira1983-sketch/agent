# Memory Fix Plan — concrete, code-anchored

Working implementation plan for repairing the agent's memory system. It sits
next to `docs/MEMORY_SYSTEM_AUDIT.md` (the audit that found the defects) and
consumes its Section D/E. **Code on `main` is the source of truth**; where this
plan and code disagree, code wins and this plan must be corrected.

> **The single most important goal of this plan** (Part B): every defect we fix
> is *also recorded into the agent's own durable memory* — as a
> `SelfImprovementIssue` with a re-runnable verifier and a `lesson` episode — so
> the autonomous agent **knows which problems it had** and can re-detect and
> re-fix them **on its own, without outside help**. A fix that only lands in git
> is invisible to the agent (this is exactly failure mode OFM-015: "recorded but
> never read").

---

## 0. Governing invariants (repo discipline, not optional)

1. **Test-first.** Every fix ships with a regression test that FAILS on the old
   code (verify by reverting) and passes on the new — `docs/self-audit-lessons.md`
   step 4.
2. **Human gate.** No unattended self-modification of behaviour; any automatic
   memory-lifecycle decision rolls out shadow → counterfactual → human-approved
   apply — `MEMORY_SYSTEM_AUDIT.md` §D.3.
3. **Self-knowledge.** Every fix is registered in the agent's own
   `SelfImprovementIssueRegistry` and journaled as a `lesson` episode (Part B).
4. **One defect = one PR.** Do not bundle unrelated fixes.

---

## Part A — WHAT & WHERE to fix (defects with code anchors)

Each row is anchored to a line and to an audit finding — nothing is "probably".

| # | Defect | Where (file:line) | Broken behaviour | Fix |
|---|---|---|---|---|
| A1 | **web_fetch → private** (MGA-06) | `core/loop.py:165` `_TOOL_SOURCE_HINTS` | `web_fetch` absent from the map → falls back to `tool_output` → `data_classifier` marks `PRIVATE`; a public page loses `public` | add `"web_fetch":"web"` + regression test. **Cleanest — first.** |
| A2 | **episode `success` from chunk counts** (MGA-02) | `core/smart_memory.py:519` `episode_from_agent_cycle` | `verified=1, unverified=10` skips `partial` (the `verified==0` clause) → `success` at quality ≈0.09; `relevance` and the confidence-gate verdict are never inputs | fold `answer_quality_score`/verified-share into the outcome; `success` requires `quality ≥ θ` |
| A3 | **quality=1.0 on empty chain** (LPF-011) | `core/smart_memory.py:41` `_compute_quality_score` | `total==0 → 1.0`: a purely self-declared / general-knowledge answer banks max quality | `total==0` → neutral floor (e.g. 0.3), not 1.0 |
| A4 | **procedure `active` from one success** (MGA-03) | `core/smart_memory.py:567` `procedure_from_episode` | gate is only `outcome=="success" and tools_used`; born `status="active"`, smoothing gives 0.667 ≥ 0.6 | add an `answer_quality_score` threshold; a new procedure is born `candidate`, not `active` |
| A5 | **bad episodes fed back as experience** (LPF-012) | `core/loop_methods2.py:159` `_retrieve_experience_memory` → `episodic_store.search(…, limit=3)` | retrieval has no outcome/quality filter — a `partial`/`quality=0` episode is injected as "experience" | filter retrieval on `outcome==success ∧ quality≥θ` |
| A6 | **no temporal-truth model** (MGA-05) | `core/models.py:150` `MemoryRecord`; ranking in `core/memory_policy.py` (token overlap + recency) | no `lifecycle_status`, `valid_from/until`, `superseded_by` relation, `confirmation_refs`; current truth, an exception, and a legacy note rank as equal facts | opt-in fields on `MemoryRecord` + intent-aware retrieval. **Largest — last, behind a flag.** |
| A7 | **no run-scoped "do not learn"** (MGA-07) | `core/loop_methods2.py:31` `_durable_learning_suppressed` (only `audit_read_only`/dry-run) | a free-text "save nothing durable" does not raise a brake; `OperatorIntentKind` has no memory-freeze | add `LearningMode = NORMAL | NO_DURABLE_LEARNING | AUDIT_READ_ONLY` (run-scoped) |
| A8 | **suspicious injection enters the chain** (MGA-04) | `core/loop.py:3110–3139` | a `suspicious` verdict is only annotated and still becomes `Evidence` → can later reach semantic memory | admit `suspicious` only as a quarantined observation, never a verified claim or semantic write |

Ordering rationale: **A1 → (A2,A3,A4,A5) → A7 → A8 → A6.** Cheap and isolated
first (A1) to validate the pipeline; then the tangled "learning-from-bad-episodes"
loop (A2–A5 are one knot); then the governance primitive (A7); then quarantine
(A8); and the invasive temporal layer (A6) last, behind a flag.

---

## Part B — Self-knowledge: record every fix into the agent's own memory ★

This is the point of the whole exercise. The mechanism **already exists** and is
wired to the REPL; today it is populated from self-build failures, not from our
manual fixes. This plan makes every A-fix (and the already-done `#9`, `LPF-001`)
a first-class, agent-visible record.

### B.1 The durable issue registry (already in code)

- `core/self_improvement_issues.py` — `SelfImprovementIssueRegistry` persists to
  `data/self_improvement_issues.jsonl`. Each `SelfImprovementIssue` carries:
  `fingerprint`, `title`, `status` (`open → verified → resolved`), `evidence`,
  `related_files`, `related_error_text`, `suggested_next_action`.
- `:self-issue-verify <fingerprint>` (main.py:1550 → `cli/commands_approval.py`)
  **re-runs the issue's targeted verifier and resolves it on green**. If the fix
  regresses, the verifier fails and the issue stays/returns `open`.

**Plan:** for each defect A1–A8 (and retroactively `#9`, `LPF-001`), register an
issue with:
- `related_files` = the source file **and** its regression test
  (e.g. A1 → `core/loop.py` + `tests/test_data_classifier_web_fetch.py`);
- `suggested_next_action` = the exact fix direction + the test command;
- `status` starts `open`; the fix lands; `:self-issue-verify` moves it to
  `resolved`.

The payoff: the agent can call `registry.unresolved()` to enumerate **its own
open problems**, each with the file to touch and the check that proves the fix —
the raw material its Stage-A/Stage-B self-task ladder (`core/self_task_producer`
/ `core/self_task_builder`) can act on **autonomously**.

### B.2 The `lesson` episodic journal (already in code)

- `core/self_build_memory.py` journals outcomes into episodic memory tagged
  `lesson` (never evicted). `_retrieve_experience_memory` already surfaces
  `search_by_tags(["lesson"], …)` (loop_methods2.py:190).

**Plan:** each fix writes a `lesson` episode ("problem class X existed here;
root was Y; the guard is test Z"). Combined with the A5 fix (filtered retrieval),
these lessons then legitimately influence later decisions — a *behaviour-change*
record, not a dead write.

### B.3 Connect the problem-history docs to the agent's self-knowledge

Today `MEMORY_SYSTEM_AUDIT.md` and `self-audit-lessons.md` are **not** in any
planner doctrine manifest — the agent cannot read its own problem history.

**Plan:** add a **thematic** group `_MEMORY_GOVERNANCE_DOC_PATHS` in
`core/planner.py` (mirroring `_SUBAGENT_GOVERNANCE_DOC_PATHS`, planner.py:255),
loaded **only** on memory/learning questions (not the universal manifest — that
would bloat every turn, per the warning at planner.py:249), guarded by a
doc-existence test. Members: `MEMORY_SYSTEM_AUDIT.md`, `self-audit-lessons.md`,
and this plan.

### B.4 The autonomous self-repair loop this enables

```
registry.unresolved()  →  the agent lists its own open problems
   →  each issue names related_files + a targeted verifier (regression test)
   →  Stage-A self_task_producer turns it into a task + the frozen test
   →  Stage-B self_task_builder writes the fix until the test passes
   →  human-gated self-apply lane applies + re-runs the suite
   →  :self-issue-verify flips the issue to `resolved`; a `lesson` episode banks why
```

Every arrow already exists in code; this plan supplies the **inputs** (registered
issues + lessons + readable doctrine) so the loop has real problems to chew on.

---

## Part C — Connect the fixed memory to the autonomous agent (wiring)

"Fixed" is dead unless it is reached at runtime (OFM-015).

- **C.1 Close the write→retrieve loop in the real runtime.** Memory is written in
  `_record_experience_memory` (loop_methods2.py:288) and read in
  `_retrieve_experience_memory` (loop_methods2.py:159). Fixes A2–A5 must be
  validated by an end-to-end tick, not only unit tests: a bad episode must no
  longer be retrieved as experience (behaviour-change assertion).
- **C.2 Reach the autonomous tick, not just the REPL.** All A-fixes live in
  `core/loop.py` / `core/smart_memory.py`, which `core/autonomous_runtime` also
  drives. Add an integration test that one autonomous pass honours
  `LearningMode` (A7) across every agent-auto sink.
- **C.3 Doctrine wiring.** As B.3 — the memory-governance docs become
  agent-readable on memory questions.

---

## Part D — Shared or divided memory? (central vs sub-agents)

Direct answer: **divided by authority, shared by reference.** Not one common
dump, not full isolation.

**Today (fact from code):** sub-agents are **memoryless** —
`core/subagent_runner.py:429–430` builds the child `AgentLoop` with `memory=None`,
`persistent_store=None`, `verifier_enabled=False`. `SubagentProposal` *declares* a
`MemoryScope(read_tags, write_tags, write_requires_review)`
(`core/subagent_memory_scope.py:71`) that is **not applied** to the child
(confirmed in `SUBAGENT_LIFECYCLE.md` §3). The contract exists on paper; the
runtime enforces nothing.

**Target topology (three tiers):**

1. **Durable memory belongs only to the centre.** Semantic (`persistent_memory`),
   episodic/procedural (`smart_memory`) stay owned by the central `AgentLoop`. A
   sub-agent **never** writes durable state directly.
2. **Sub-agents get scoped read + write-as-proposal.** Enforce `MemoryScope`
   end-to-end in `subagent_runner`: the child receives a **read-only slice
   filtered by `read_tags`** (context injection, not the store), and any
   "conclusion" the child reaches becomes a **proposal** routed to the centre's
   approval inbox / verifier — not memory. This is CENTRAL_AGENT_GOVERNANCE §3's
   principle made real: a sub-agent answer is a witness, not a fact.
3. **The shared layer is a coordination ledger, not semantics.** Per
   `MULTI_AGENT_COORDINATION_LAYER.md`: an append-only Agent Mail / Decision Log /
   Evidence-links store where agents exchange **identifiers** (REQ-/DEC-/VER-),
   each record carrying a **trust class** (`agent_message` < `tool_observation` <
   `verified_result` < `human_approved_decision`). Shared, yes; trusted, no.
   Promotion from the ledger into durable memory goes **only** through the
   centre's write policy + verification.

Formula: **the centre owns truth; sub-agents read narrow slices and propose; a
shared append-only ledger carries messages/decisions/evidence by ID — but
"shared ≠ true".**

Sequencing note: enforce `MemoryScope` starting from an **empty read-only scope**
(preserves today's `memory=None` behaviour), then widen `read_tags`
incrementally, each step guarded by a "child sees only its `read_tags`" test.

---

## Part E — Sequencing (each step: a failing test AND a registered issue)

| Phase | Content | Acceptance test (fails on old code) | Self-knowledge (Part B) |
|---|---|---|---|
| **P0** | A1 (web_fetch→public) | a public page via `web_fetch` classifies `public` | register + resolve issue A1 |
| **P1** | A2+A3+A5 (learning loop) | `verified=1, unverified=10` → episode **not** `success`; a low-quality episode is **not** retrieved as experience | issues A2/A3/A5 + `lesson` episodes |
| **P2** | A4 (procedure) | one low-quality success does **not** create an `active` procedure (born `candidate`) | issue A4 |
| **P3** | A7 (LearningMode) | run-scoped `NO_DURABLE_LEARNING` → Δ=0 across every agent-auto sink; `:remember` (user-explicit) separately specified | issue A7 |
| **P4** | A8 (suspicious quarantine) | `suspicious` content admitted only as a quarantined observation, never verified/semantic | issue A8 |
| **P5** | B.3 + C.2/C.3 (connection) | memory docs load in the thematic manifest (existence guard); an autonomous tick honours `LearningMode` | — |
| **P6** | D (sub-agent topology) | a sub-agent cannot write durable; its write becomes a proposal; it reads only the `read_tags` slice | issue for the enforcement gap |
| **P7** | A6 (temporal truth) — **behind a flag, shadow-first** | retrieval does not present superseded/proposed/historical records as equal current facts | issue A6 |

P0–P4 are deterministic small PRs. P5–P6 are integration/architecture. P7 is
invasive: `lifecycle_status` defaults to `unclassified`, retrieval unchanged for
`unclassified`, a shadow phase, and supersession only under human approval.

---

## Part F — Plan self-review (honest risks)

**Strengths.** Anchored to code (file:line + audit id), ordered by risk (cheap
A1 validates the pipeline before the invasive A6), and it obeys the repo's
test-first / human-gate / shadow-rollout discipline. The self-knowledge component
(Part B) reuses machinery that already exists rather than inventing a store.

**Risks and where the plan can break:**
1. **A2/A4 — the threshold θ is arbitrary.** A hand-picked quality cutoff can
   start discarding legitimate general-knowledge answers (ties to LPF-002/A3:
   general knowledge has `evidence_score=0`). *Mitigation:* calibrate θ on real
   episode logs, shadow-first (log the new outcome without changing behaviour,
   compare), not a constant from thin air.
2. **A6 — the most dangerous.** Auto-marking `superseded`/`invalidated` on one
   model inference risks silently hiding a correct record. *Mitigation (hard, §D.3):*
   default `unclassified`; retrieval unchanged for it; supersession only under
   human-approved apply; never hide a record on a single pass.
3. **D — enforcing `MemoryScope` can break current sub-agents.** They run
   `memory=None` today; enabling scoped-read could leak extra context into the
   child. *Mitigation:* start from an empty read-only scope; widen `read_tags`
   incrementally with a scope-isolation test.
4. **B.3 — context bloat.** Wire the memory docs into the **thematic** (not
   universal) manifest, or the agent loads them on every unrelated question.
5. **Hidden coupling A3 ↔ LPF-002.** The quality floor and "general knowledge has
   zero evidence" are the same nerve; fix them consistently or one undoes the
   other.

**What the plan deliberately does NOT do.** It does not touch `user-explicit
:remember` (kept allowed even in audit mode — MEMORY_SYSTEM_AUDIT §C); it does not
give sub-agents identity/budget (that is `docs/future/CORPORATE_MODEL.md`, out of
scope).

---

*Provenance: anchors verified against code on `main`. Source-of-truth precedence:
code > this plan. Companion documents: `docs/MEMORY_SYSTEM_AUDIT.md` (findings),
`docs/self-audit-lessons.md` (fixed classes), `docs/SUBAGENT_LIFECYCLE.md` and
`docs/MULTI_AGENT_COORDINATION_LAYER.md` (sub-agent memory topology).*
