# Memory Map — Stage M0 (read-only foundation for the memory-lifecycle reconstruction)

Complete read-only map of the agent's memory subsystem: every store, where it is
**written**, where it is **read**, whether it actually **influences a decision**,
which production paths it reaches, and where data is written but never used
(dead sinks). This is the required input to M1 (design of a single, coherent
memory lifecycle). **No code was changed producing this.**

- **Audited commit:** `main` @ `f317c4c`.
- **Method:** static reading + call-site census of `self.<store>.<method>` across `core/`, `app/bootstrap.py`, `agent_tick.py`, `main.py`; cross-checked against the M0 traces already in `MASTER_ISSUE_REGISTRY.md`.
- **Verification limit:** call-site census + reading, **not** a live multi-turn run. "Influences decisions?" is judged from the read sites found; a store read only by an operator `:command` is marked *status-only* (does not steer the agent).

---

## 1. The seven memory stores + evidence chain

| Store | Written by | Read by (decision path) | Influences a decision? | Verdict |
|---|---|---|---|---|
| **Working memory** (`core/memory` — turns, artifacts, tool-output cache) | `record_turn`, `cache_store` | `recent_turns`, `conversation_context`, `cache_lookup`, artifacts→provenance chain (`loop.py:1331`) | **Yes** — history into planner/synth, cache reuse, `[memory:]` evidence | **LIVE** (interactive only) |
| **Persistent / semantic** (`core/persistent_memory`) | `:remember` (user-explicit); knowledge-pipeline **agent-auto** (`loop.py:1394`) | `_retrieve_persistent` → `<long_term_memory>` into planner+synth + into the chain (`loop.py:1305`) | **Yes** — LTM block, citable `[memory:]` evidence | **LIVE** |
| **Episodic** (`core/smart_memory`) | `_record_experience_memory` every cycle (`loop_methods2`) | `_retrieve_experience_memory` (`search`, `find_most_similar`), **fast-path** (`loop.py:764`), **re-ask** (`:236`) | **Yes** — experience block, verbatim replay, repeat hint | **LIVE interactive; ABSENT on the autonomous path** |
| **Procedural** (`core/smart_memory`) | `upsert_from_episode` (`loop_methods2:369`) | `_retrieve_experience_memory` → `procedural_store.search` (`loop_methods2:214`) | **Yes** — injected into planner as reusable workflows | **LIVE interactive; ABSENT autonomous** |
| **Consolidation** (`core/smart_memory`) | `consolidate_memory` every cycle (`loop_methods2:382`) | **only** `smart_memory_summary` (`:416`, CLI `:smart-memory`) | **NO** | **DEAD SINK for decisions** (status-only) |
| **User profile** (`core/user_profile`) | `update` on durable-learning cycles (`loop.py:2110`) | `load_or_default` at cycle start (`loop.py:494`) | **Yes** — language, verbosity, expertise level | **LIVE** |
| **Assumptions** (`core/assumption_registry`) | `save_many` per run (`loop.py:512` area) | `load_by_run(run_id)` | **Weak** — one-shot uses a fresh `run_id` each turn → effectively per-run scratch (LPF-017/MIR-027) | **LIVE but run-scoped** |
| **Source registry** (`core/source_registry*`) | `save_registry` every cycle (`knowledge_pipeline`, `loop.py:1397`) | operator CLI (`:source-registry`, `:conflicts`) + `conflict_review`; **not** re-read into later answering | **NO** (audit/conflict store; not a retrieval source for answers) | **Near-dead sink for the answering path** |

> The **provenance chain** (`core/evidence`) is the join point: persistent records
> (`loop.py:1305`) and working-memory artifacts (`:1331`) are injected into it every
> cycle so the verifier can resolve `[memory:*]` citations. This is where memory
> meets verification (see §4).

---

## 2. Which production path actually has memory (the biggest integration gap)

Memory wiring is decided in `app/bootstrap.py:143-158`. **Episodic, procedural and
consolidation stores are created only under `if with_memory:` — NOT under
`with_persistent`.**

| Production entry point | `build_agent(...)` | Working | Persistent | Episodic | Procedural | Consolidation | User-profile |
|---|---|---|---|---|---|---|---|
| **Interactive REPL / `--ask`** (`main.py:2079`) | `with_memory=True` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Autonomous runtime / daemon / `agent_tick`** (`main.py:2017`, `agent_tick.py:806`) | `with_memory=False` | ❌ | ✅ | **❌** | **❌** | **❌** | ✅ |
| **Sub-agents** (`core/subagent_runner:429`) | `memory=None, persistent_store=None` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

**Consequence:** the entire episodic/procedural learning system — experience
retrieval, the fast-path, re-ask detection, procedure distillation, consolidation
— runs **only in the interactive path**. The **unattended agent** (the daemon,
campaigns, scheduled tasks — the whole point of the autonomy work) records **no
episodes, builds no procedures, consolidates nothing, and has no working memory**.
It keeps only persistent/user-profile/assumptions/source-registry. So today the
agent that runs alone does not learn from its own experience; the agent that a
human drives does. Sub-agents are fully memoryless by design.

This is the single most important thing M1 must resolve: **one lifecycle wired
into every path**, not a learning system that only exists when a human is watching.

---

## 3. Dead / near-dead sinks (written but not used for decisions)

1. **Consolidation — dead sink.** `consolidate_memory` runs every interactive cycle
   and saves a report (`active/needs_review/obsolete` procedure ids, notes). The
   report is read **only** by `smart_memory_summary` for the `:smart-memory` CLI. It
   is a pure computation over the procedures' own `status`; it does **not** mutate
   procedures and **nothing** consumes it to steer retrieval, planning, or pruning.
2. **Source registry — near-dead for answering.** Written every cycle from the run's
   claims; consumed by operator CLI + `conflict_review`; **not** retrieved back into
   later answers. It is an audit/provenance store, not a decision input.
3. **Assumptions — run-scoped.** `load_by_run(run_id)` with a fresh `run_id` per
   one-shot turn → written but only visible within the same run (MIR-027).
4. **Hygiene is manual-only.** `prune_episodic`, `archive_low_value_memory`,
   `expire/dedup/summarise` (`core/loop_methods.py`) are invoked **only** by the
   `:hygiene` CLI command — never automatically, and never on the autonomous path.
   Without an operator running it, episodic/persistent memory grows unbounded (this
   is the mechanism behind the earlier auto-memory pollution incident).

---

## 4. Where "unverified" is laundered into "verified" (self-reinforcement)

Two independent vectors, both now traced end-to-end:

- **Episodic (MIR-041):** the fast-path replays a stored answer verbatim, skips
  verify, and re-banks it with `verified_chunks=1`; the replay is itself
  fast-path-eligible → a self-reinforcing "verified success" chain. Interactive path
  only (episodic memory is off autonomously).
- **Semantic (MIR-042) — verifier question now RESOLVED.** `core/verifier_core.py:87-116`:
  a citation that `match_citation`s **any** evidence in the chain (including
  `kind="memory"` evidence) is marked `[verified:…]` and increments `verified_chunks`
  (line 114-116). Every retrieved persistent record and working artifact is injected
  into the chain uniformly, with **no trust-class filter** on `source`
  (`loop.py:1305-1347`). So a `[memory:<id>]` citation to a stored record counts as
  *verified* — the verifier confirms the citation *resolves to a real record*, not
  that the record's *content* was ever independently checked.
  - **Severity is higher than first assumed:** `knowledge_auto_write` defaults to
    **`True`** in production (`app/bootstrap.py:250`, env `AGENT_KNOWLEDGE_AUTO_WRITE`),
    so the knowledge pipeline **auto-writes agent-generated claims to persistent
    memory by default**. Those agent-auto records, retrieved and cited later, then
    count as verified. Partial guards: the memory **write policy** (`decide` per
    claim), the **echo antibody** (`core/memory_echo_antibody`, wired in `loop.py`),
    and `AGENT_FREEZE_AUTO_MEMORY`. Net: the semantic laundering loop is *active by
    default*, throttled but not closed, and it has **no** verification-provenance on
    the record itself.

---

## 5. Duplicate / overlapping / inconsistent mechanisms (M1 must unify)

- **Two tokenizers (MIR-008):** `smart_memory._tokens` (`len>2` OR digit, no stopwords)
  vs `memory_policy._TOKEN_RE` (`len>1` + stopwords). Episodic and persistent stores
  key on the same text differently — and this is exactly the boundary where short
  acronyms (`AI`/`ML`/`os`) are dropped by one and kept by the other (MIR-007).
- **`verified_chunks` overloaded:** an **episode** field (a stored count, sometimes
  synthesised — MIR-041) *and* a **verifier-report** field (a computed truth). Same
  name, different trust. The episode's count feeds `_compute_quality_score`.
- **Three similarity gates on one episodic store, three thresholds/semantics:**
  experience retrieval (outcome/lesson filter), re-ask (`Jaccard≥0.40`), fast-path
  (`Jaccard≥0.85` + `quality≥0.70`). No shared notion of "is this the same question"
  or "is this episode trustworthy."
- **Quality vs verification conflated at the reader (MIR-002):** `answer_quality_score`
  (`1.0` on an empty chain) is used as a proxy for "was this verified" by the
  fast-path — the exact conflation the M1 two-axis provenance (`verification_status`
  × `response_origin`) must remove.

---

## 6. Integration matrix — memory × agent stage

Where each cognitive stage touches memory (✅ wired, ⚠️ partial/one-path, ❌ absent):

| Stage | Working | Persistent | Episodic | Procedural |
|---|---|---|---|---|
| **Observe / retrieve** | ✅ recent_turns/context | ✅ `_retrieve_persistent` | ⚠️ `_retrieve_experience` (interactive only) | ⚠️ `search` (interactive only) |
| **Plan** | ✅ history | ✅ LTM block | ⚠️ experience block | ⚠️ procedures block |
| **Act** | ✅ cache_store/lookup | — | — | — |
| **Verify** | ✅ artifacts→chain | ✅ records→chain (⚠️ no trust filter, MIR-042) | — | — |
| **Fast-path** | — | — | ⚠️ replay (MIR-002/041) | — |
| **Re-ask** | — | — | ⚠️ Jaccard≥0.40 (MIR-024) | — |
| **Hygiene** | — | ⚠️ manual `:hygiene` only | ⚠️ manual only | — |
| **Learn (write-back)** | ✅ record_turn | ✅ auto-write (default ON) | ⚠️ episode/procedure (interactive only) | ⚠️ (interactive only) |
| **Consolidate** | — | — | ➡️ report | ➡️ report (dead sink §3.1) |

---

## 7. Inputs this hands to M1 (design of the single lifecycle)

1. **One provenance vocabulary across all four stores** — the two-axis scheme
   (`verification_status` × `response_origin`) generalised from episodes to
   persistent/working/procedural records, plus a **trust class** on each record
   (user-approved vs agent-auto vs replay vs ingested-source) so §4's laundering is
   structurally impossible.
2. **Wire the lifecycle into every path** (§2): the autonomous/daemon agent must
   participate in episodic/procedural learning (or the decision to keep it out must
   be explicit and safe), not silently run memoryless.
3. **Retire or repurpose the dead sinks** (§3): make consolidation actually gate
   something or drop it; give source-registry a retrieval path or scope it as audit;
   fix assumptions' run-scoping; decide automatic vs manual hygiene.
4. **Unify the duplicated mechanisms** (§5): one tokenizer, one "same-question"
   notion, one meaning of `verified_chunks`, quality separated from verification.
5. **Close both laundering vectors together** (§4): MIR-041 (episodic replay) and
   MIR-042 (semantic citation) share the root "memory presence ≠ verification."
6. **Safe migration**: legacy records default to `verification_status=not_run` /
   `response_origin=generated`, no guessing from old fields; roll out flag-gated and
   shadow-first (MIR-040 lesson).

---

## 8. Scope limits / still-open threads

- The census is code-level; a live multi-turn run (interactive AND autonomous) is
  needed to confirm the autonomous memory-blindness end-to-end and to measure the
  semantic auto-write volume in practice.
- `conflict_review` and the ingestion path (`ingestion*`, `knowledge_pipeline`
  write policy internals) were mapped at the boundary, not line-by-line.
- The verifier's exact `match_citation` semantics for `kind="memory"` were read
  (verified counts) but not exercised with a crafted chain — a focused verifier test
  belongs to group 3.4.

---

## 9. Boundary trace A — verifier trust (group 3.4, `[memory:<id>]`)

Focused test `tests/test_verifier_memory_trust.py` (2 fail-before + 2 proof, run against `main`):

- **A matched `[memory:<id>]` citation counts `verified`** (`verifier_core.py:114-116`) — the verdict tracks **citation resolution, not content**. Proved: the *same wrong content* ("capital of Australia is Sydney") scores `verified` when the record is in the chain and `cited_but_unmatched` when it is not.
- **No trust distinction in the verdict.** A `user-approved` and an `agent-auto` memory record cited together both return `verified` (`['verified','verified']`). A **working-memory artifact** counts verified via the identical path. The verifier ignores evidence `kind`, `obtained_via`, `confidence`, and the record's `source`. → **MIR-046** (kept separate from MIR-042).

## 10. Boundary trace B — persistent-record creation, trust & conflict

**Every path that creates a persistent record, with its trust today:**

| Path | Written via | `source` today | Gate that actually runs |
|---|---|---|---|
| Operator `:remember` | `loop_methods.remember` → `persistent_store.save` | `user-explicit` | `MemoryWritePolicy` (echo-**exempt**; never frozen) |
| Answering auto-write | knowledge pipeline → `remember` | `agent-auto` | `KnowledgeWritePolicy.decide` **+** `MemoryWritePolicy`+echo |
| Ingestion (`:ingest-*`) | `_remember_from_knowledge` → pipeline | source-typed | same as auto-write |
| Self-repair | `agent.remember` | (repair) | `MemoryWritePolicy` |

- **`KnowledgeWritePolicy.decide` (real gate, `knowledge_pipeline.py:283`)** rejects: source not registered, empty, too long, secret, **hype**, **`status ∈ {unverified, conflicted}`**, **confidence `< 0.55`**, **source `trust_level < 0.55`**. So raw unverified answers are **not** auto-written — the write side is well-guarded.
- **Claim `status` is promoted to `verified` by SOURCE CORROBORATION** (`knowledge_pipeline.py:261`), a *different* notion than the answer verifier. Two "verified" meanings coexist.
- **`echo-antibody` has real influence:** `recent_writes` is a live input to `write_policy.decide` (`loop_methods.py:134`); an echoing write can be rejected. Not inert.
- **`conflict_review` only reports (MIR-047):** `ConflictReview.review` builds suggestions, called by `:conflicts`, logged — it does **not** tag or persist status. Retrieval filters only `QUARANTINE_TAGS`, which conflict_review never sets → a claim that becomes conflicted *after* being written stays retrievable and citable as `verified` until an operator acts.

## 11. Real provenance / trust vocabularies that exist TODAY (fragmented — M1 must unify)

There is **no** `response_origin` or `trust_class` field. Trust is scattered across five separate vocabularies:

- **Evidence `kind` → confidence** (`DEFAULT_CONFIDENCE`, `evidence.py`): `user_explicit 1.0 · test_result .95 · file .90 · log_event .90 · shell_output .85 · diff_preview .80 · web_page .75 · tool_output .70 · memory .55 · web_search_hit .35 · llm_claim .20 · unknown .10`.
- **Source `trust_level`** (`DEFAULT_SOURCE_TRUST`, per `SourceType`): `user 1.0 · test_result .95 · file .90 · log .88 · code_repository .86 · official_site .84 · documentation .82 · pdf .78 · book .76 · article .70 · web_page .65 · video .60 · podcast/memory .55 · forum .45 · …`.
- **Claim `status`**: `extracted · verified · conflicted · unverified`.
- **Persistent record `source`** (trust-meaningful values): `user-explicit` vs `agent-auto` (+ ingested source types).
- **`QUARANTINE_TAGS`**: `quarantine · do-not-use · wrong · obsolete · superseded · temporary · transient`.

**De-facto response origins today** (implicit, via `source_labels` / code path, no field): `generated` (normal answer), `memory_replay` (fast-path, `source_labels=[memory:<id>]`), `refusal`, and for persistent records `user-explicit` / `agent-auto` / `ingested`.

## 12. The write → read → "counts as verified" matrix

| Record class | Who can WRITE it | Who READS it (decision path) | Counts as `verified` when cited? |
|---|---|---|---|
| **user-approved** persistent (`:remember`) | operator | `_retrieve_persistent` → LTM block + chain | **Yes** (legitimately) |
| **agent-auto** persistent (auto-write) | agent (gated: status≠unverified, conf≥.55, trust≥.55, not hype/secret; echo-guarded) | same | **Yes** — even though "verified" here means *source-corroborated*, not answer-verified (MIR-046) |
| **ingested-source** persistent | `:ingest-*` | same | **Yes**, same path |
| **conflicted** persistent (post-write) | (was written pre-conflict) | still retrievable (not quarantined) | **Yes** until operator acts (MIR-047) |
| **quarantined/superseded** persistent | operator tags | **filtered out** (`KnowledgeUsePolicy`) | No (correctly excluded) |
| **working-memory artifact** (tool output) | agent | artifacts → chain | **Yes** (legit tool evidence, but indistinguishable from the above) |
| **episodic replay** (`memory:<id>`) | fast-path | re-banked `verified_chunks=1`, re-fed | **Yes** (self-reinforcing, MIR-041) |

**One-line reading:** *anything that can be cited and resolves in the chain counts as `verified`; the only records that are excluded are the ones an operator has manually tagged `QUARANTINE_TAGS`.* That is the single fact M1's trust model must change.

## 13. New defects from the boundary traces (registered separately, not merged with MIR-042)

- **MIR-046** — verifier verdict is trust-blind (any resolved citation → `verified`).
- **MIR-047** — conflict_review is advisory-only; a conflicted record stays citable as verified (write side is otherwise well-guarded).

## 14. POST-M0 ADDENDUM (added 2026-07-19, AFTER M0 was accepted) — the procedural WRITE-BACK loop is broken

> **Process note:** M0 (§1–§13) was accepted by the operator before this section existed.
> This is a **labeled post-acceptance addendum** from the M1 review — not part of the
> accepted M0 baseline, and recorded here so the accepted map is never silently mutated.
> The findings are registered as MIR-048 / MIR-049 / MIR-050 (the latter
> `needs_investigation`); the authoritative status lives in the registry.

§1 marks procedural memory "influences a decision? **Yes**" — true for the **retrieval**
side (procedures are injected into planning). The M1 review proved the **write-back** side
is not a loop at all:

- non-success episodes never reach a procedure (`procedure_from_episode` → `None` before
  `with_episode`; `failure_count` increment is dead in production) → confidence is a
  one-way ratchet from 0.667, demotion unreachable (**MIR-048**);
- procedure *usage* is never attributed to the cycle's outcome (`_last_procedure_records`
  is write-only) (**MIR-049**);
- procedure identity is the tool sequence alone, so aggregated evidence spans unrelated
  goals (**MIR-050**).

Net: procedural memory *steers* planning but is itself steered only by coincidental
same-tool-sequence successes — a positive-only, unattributed, weakly-identified signal.

---

*Provenance: read-only against `main` @ `f317c4c`. Code > this map. Companion:
`docs/audit/MASTER_ISSUE_REGISTRY.md` — **its tally is the authoritative issue count**
(any MIR list or count embedded in this map is a snapshot) — and
`docs/audit/AUDIT_PROGRESS.md`. §14 is a labeled post-M0 addendum, not accepted-M0 text.*
