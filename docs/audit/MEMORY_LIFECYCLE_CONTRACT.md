# Memory Lifecycle Contract — M1 (canonical design, **v2-draft**)

> **Status: v2-draft — awaiting operator approval. No code implements this document.**
>
> **Version log (amendments happen HERE — no parallel design docs):**
> - **v1-draft — REJECTED (operator, 2026-07-19).** Recorded faults: (1) the header
>   rejected consolidation Option A while the normative body (§5/§8/§10/§11/§14/§15)
>   continued to prescribe it; (2) `conflicted` was conflated with procedure
>   `needs_review`; (3) the closed-loop procedural design existed only as chat analysis,
>   not contract text; (4) unresolved forks were presented as prescriptions.
> - **v2-draft (this).** Option A removed from the entire normative body; consolidation
>   **retirement** is the single prescription; the closed-loop attributed bidirectional
>   procedural lifecycle is written as normative §6.4 + §7.7; `conflicted` is reserved for
>   genuine contradiction (degradation demotes to `extracted`); every unresolved decision
>   is isolated in §16 "Open decisions" and nothing unresolved is phrased as prescription.

- Baseline: `main` @ `f317c4c`.
- Built on: completed M0 (`docs/audit/MEMORY_MAP.md` incl. its labeled post-M0 addendum §14)
  and the registry (`docs/audit/MASTER_ISSUE_REGISTRY.md` — the authoritative status
  owner; its count is generated, so this document states no number).

> **⚠️ ADDENDUM (2026-07-20) — this contract has been partly overtaken by events.**
>
> It was never approved, and much of what it planned was then delivered by
> direct fixes on `fix/mir-043-autonomous-experience-wiring` rather than by
> executing its phases. Closed **outside** this contract, each with its own
> fail-before tests: MIR-043 (autonomous wiring), MIR-002 (undefined quality
> score), MIR-041 (replay banking), MIR-046 (trust-aware verification),
> MIR-047 (conflict quarantine), MIR-049 (procedure attribution), MIR-048
> (procedural feedback), MIR-051 (legacy confidence migration).
>
> Consequence for anyone reading further: the "closes MIR-xxx" annotations in
> §6–§11 and the P0–P8 phase plan in §15 describe intent from before those
> fixes existed. **Do not plan work from them without checking the registry
> first** — several phases would re-implement something already shipped, and
> the mechanisms chosen differ from what this draft proposed (eligibility is
> `usage_eligible`, not the six-dimension envelope; attribution is
> `used_procedure_ids`, not the D1–D6 scheme).
>
> Still live and not superseded: §16 open decisions D-1 (procedure identity —
> measured and deliberately deferred, see MIR-050) and D-2 (autonomous
> fast-path), plus the parts covering MIR-044/045-adjacent dead sinks and the
> temporal-truth model (MIR-009), none of which were touched.
>
> This note is deliberately additive: the draft is left intact as the record of
> what was designed, per the doc-state discipline that a rejected or overtaken
> document is annotated, not silently rewritten.
- Companion evidence: fail-before tests `tests/test_fast_path_verification_provenance.py`
  (7) and `tests/test_verifier_memory_trust.py` (4) — already written; red/green exactly as
  designed.
- The registry's earlier "Fix-design contract (MIR-002+041)" section remains the historical
  defect-level record; **this document is canonical** where they differ.

---

## 1. Purpose and scope

One coherent memory lifecycle across the **existing** four memory families — working,
persistent/semantic, episodic, procedural (plus the audit-scoped source registry and the
provenance chain as the join point) — wired into **every** production path (interactive,
autonomous/daemon, sub-agents), such that:

- запись существует ≠ запись истинна (a record existing ≠ the record being true);
- источник доверенный ≠ утверждение проверено (a trusted origin ≠ a verified claim);
- повтор из памяти ≠ новая проверка (a memory replay ≠ a new verification);
- конфликт обнаружен → запись больше не используется как обычное доказательство
  (a detected conflict → the record stops serving as ordinary evidence).

**Out of scope (explicitly):** a new store; a second verifier; the full temporal-truth
model of MEMORY_FIX_PLAN A6 (`valid_from/until`, `confirmation_refs`) — reserved fields
only; sub-agent persistent identity (`docs/future/CORPORATE_MODEL.md`).

## 2. The six independent dimensions

Six dimensions, **never merged, never derived from one another** except where a rule below
says so explicitly. The five existing vocabularies (evidence-kind confidence, source
trust_level, claim status, record source, quarantine tags) are **not replaced** — they
remain as measurement inputs; the envelope makes their roles explicit.

### D1 — `response_origin` (where the content came from; immutable at creation)
`generated` · `user_explicit` · `agent_auto` · `ingested_source` · `memory_replay` ·
`working_artifact` · `subagent_result` · `refusal` · `system_internal`
(the last two confirmed by M0: refusal-path episodes; internal writes like
`repair`/`profile`/`builtin`). Closed list; extension = contract amendment.

### D2 — `verification_status` (did the verifier actually check this content?)
`not_run` (default) · `verified` · `partially_verified` · `unverified` · `verifier_error`.
Set **only** from a real verifier run over the content and its evidence (§5 authority).
`verifier_error` is what the soft-fail path (`loop.py:1533`) must record — never a clean
default.

### D3 — `trust_class` (trust in the *origin/source*, never proof of content truth)
`operator_approved` · `tool_observation` · `corroborated_source` · `external_source` ·
`agent_derived` · `replay_echo`.
Derived **deterministically at write time** from D1 + the source record (derivation table
§4.3). Repetition/echo never upgrades it.

### D4 — `claim_status` (knowledge-record lifecycle; not for event records)
`extracted` · `active` · `conflicted` · `superseded` · `obsolete` · `rejected`.
Applies to **knowledge records** (persistent claims/facts, procedures). **Event records**
(episodes, working turns/artifacts) are immutable happenings and carry no claim_status.
Two naming disciplines:
- the source-registry's corroboration-"verified" is renamed **`active`** — the word
  "verified" is reserved exclusively for D2;
- **`conflicted` means genuine contradiction only** (contradicting claims, contradictory
  same-key procedures, an operator dispute). It is **never** a synonym for "low
  confidence / needs review" — evidence-degradation demotes to `extracted` (§6.4), decay
  goes to `obsolete`. (v1 fault #2, fixed.)

### D5 — `usage_eligibility` (the enforcement switch retrieval MUST consult)
`allowed` · `restricted` · `quarantined` · `blocked`.
Stored on the record; changed only by defined transitions (§6.3). `restricted` = usable
only as explicitly-labeled *disputed context*, never ordinary evidence. `quarantined` =
excluded from retrieval/prompts/chain (operator-visible for audit). `blocked` = terminal
exclusion (operator only).

### D6 — evidence confidence / weight (numeric; ranking only)
The existing numbers — `DEFAULT_CONFIDENCE` per evidence kind, source `trust_level`,
claim `confidence` — stay as **ranking/threshold inputs** (write-policy minimums,
retrieval ordering). **Never a gate for D2**: no confidence value, however high, makes
anything `verified`.

## 3. The master invariant

> **Neither origin, nor trust_class, nor confidence, nor the fact that a record exists —
> alone or in combination — may ever produce `verification_status = verified`.**
> Citation resolution means only: *"the cited record exists and matches the identifier."*
> It never means: *"the record's content was independently verified."*

Everything in §7 (decision rules) is an application of this invariant.

## 4. Canonical record schema — **Artifact 1**

### 4.1 The envelope (added to every record in all four stores; additive, JSON-safe)

```
MemoryEnvelope v1
  schema_version:       1
  response_origin:      D1 enum            # immutable
  verification_status:  D2 enum            # default not_run
  trust_class:          D3 enum            # derived at write, stored
  usage_eligibility:    D5 enum            # default allowed
  source_episode_id:    str | null         # replays & derivations: link to origin record
  task_id:              str | null         # RuntimeTask linkage (§9.4)
  envelope_updated_at:  iso8601
  # Reserved, NOT implemented in v1 (MEMORY_FIX_PLAN A6 / MIR-009):
  #   valid_from, valid_until, superseded_by, confirmation_refs
```

### 4.2 Per-family overlay

| Family | Record kind | Extra lifecycle fields | Notes |
|---|---|---|---|
| Working (turns, artifacts) | **event** | — | envelope only; artifacts entering the chain carry their envelope with them |
| Episodic (`EpisodeRecord`) | **event** | keeps `outcome`, chunk counts, `answer_quality_score`; **adds `used_procedure_ids`** (§7.7) | `verified_chunks` becomes **verifier-reported only** — never synthesised (§7.3) |
| Persistent (`MemoryRecord`) | **knowledge** | `claim_status` (D4), existing `tags`/`source` kept | `source` field remains; D1/D3 make its meaning explicit |
| Procedural (`ProcedureRecord`) | **knowledge** | `claim_status` (D4); counters per §6.4 (`verified_success_count`, `negative_count`) | birth = `extracted` (never `active`); legacy `needs_review` maps → `extracted` (§13) |
| Source registry (claims) | **knowledge (audit-scoped)** | D4 with `active` replacing corroboration-"verified" | registry stays an audit store (§10) |

### 4.3 trust_class derivation (deterministic, write-time)

| response_origin (+context) | trust_class |
|---|---|
| `user_explicit` | `operator_approved` |
| `working_artifact` (real tool output), test/log/file/shell evidence | `tool_observation` |
| `ingested_source` with independent corroboration (pipeline rule) | `corroborated_source` |
| `ingested_source` single-source | `external_source` |
| `generated` / `agent_auto` / `subagent_result` | `agent_derived` |
| `memory_replay` | `replay_echo` |

## 5. Authority — who may change what

| Actor | May set / change | May NEVER |
|---|---|---|
| **Verifier** (the one existing verifier) | D2 on the content it actually examined | change D1, D3, D4, D5 |
| **Knowledge pipeline** | create knowledge records; D4 `extracted → active` via corroboration (claims) | set D2 at all (corroboration is D4, not D2) |
| **Conflict detector** (existing conflict_review logic, promoted to enforcement) | D4 `→ conflicted` **and** D5 `→ restricted` (automatic, reversible) | delete, reject, supersede, or block anything |
| **Procedural feedback (store update path, under Loop)** | procedure counters from **attributed** episodes (§7.7); automatic `extracted ⇄ active` per §6.4 thresholds | set D2; set `conflicted`; count unattributed episodes or unverified successes |
| **Operator** | D4 `conflicted → active/superseded/rejected`; D5 any transition incl. `quarantined/blocked` and restores; `:remember`/`:forget` | — (human holds the widest authority) |
| **Hygiene** | D4 `→ obsolete` (staleness, incl. **procedures with no attributed events for T**); archive flag | touch `operator_approved` records outside existing curated-protection rules; set D2 |
| **Fast-path (replay writer)** | create replay **event** records only (D1=`memory_replay`, D2=`not_run`, D3=`replay_echo`) | set D2=`verified`, write `verified_chunks>0`, upgrade any source record |
| **Sub-agent runner** | deliver results as parent-side proposals (D1=`subagent_result`) | write durable memory directly (unchanged: memoryless) |
| **Migration** | stamp envelope defaults on legacy records (§13) | infer D2/D4 from legacy indirect fields |

*(v1's "Consolidation" actor row is removed: consolidation is retired, §8.)*

## 6. State-transition tables — **Artifact 2**

### 6.1 `verification_status` (D2)
```
not_run ──verifier ran, all support held──────────▶ verified
not_run ──verifier ran, part held─────────────────▶ partially_verified
not_run ──verifier ran, nothing held──────────────▶ unverified
not_run ──verifier raised──────────────────────────▶ verifier_error
verifier_error ──later successful re-run───────────▶ verified | partially_verified | unverified
(unverified/partially_verified may be re-verified upward ONLY by a new verifier run)
FORBIDDEN: any transition into `verified` not initiated by the Verifier actor.
Replays: frozen at not_run forever (a replay is never re-examined content).
```

### 6.2 `claim_status` (D4, knowledge records)
```
extracted ──corroboration (pipeline; claims only)─▶ active
extracted/active ──conflict detector──────────────▶ conflicted        [auto; also D5→restricted]
conflicted ──operator resolves────────────────────▶ active | superseded | rejected
active ──newer accepted claim + operator/policy───▶ superseded
active/extracted ──hygiene expiry/staleness───────▶ obsolete
any ──operator────────────────────────────────────▶ rejected
FORBIDDEN: superseded/obsolete/rejected → active without an OPERATOR action;
automatic supersession on a single model inference (MEMORY_SYSTEM_AUDIT §D.3);
`conflicted` used for anything but genuine contradiction (low evidence = `extracted`).
```

### 6.3 `usage_eligibility` (D5)
```
allowed ──conflict detector───────────────────────▶ restricted        [auto, reversible]
allowed/restricted ──operator or QUARANTINE_TAGS──▶ quarantined
restricted ──operator resolves conflict───────────▶ allowed
quarantined ──operator restore────────────────────▶ allowed
any ──operator────────────────────────────────────▶ blocked           [terminal]
Derived rule: D4 ∈ {superseded, obsolete, rejected} ⇒ D5 ≥ quarantined (enforced on read).
FORBIDDEN: any automatic transition INTO allowed; automatic blocked.
```

### 6.4 Procedure lifecycle — **closed loop (normative; replaces v1's tally-driven §6.4)**

Counters (per procedure, fed **only** by attributed episodes, §7.7):

```
verified_success_count += 1   iff attributed episode: outcome == success AND D2 == verified
negative_count         += 1   iff attributed episode: outcome == failed,
                              OR usage-attributed cycle whose answer ended
                              unverified / partially_verified (D2)
confidence = Beta(1,1)-smoothed(verified_success_count, negative_count)
```

Transitions:

```
birth ▶ extracted                                   (never active — closes MIR-003 birth gate)
extracted ──verified_success_count ≥ 2 AND confidence ≥ 0.6──▶ active      [auto, §5]
active ──confidence < 0.6 on any attributed event──▶ extracted             [auto — demotion
                                                     is REACHABLE; closes MIR-048]
extracted/active ──genuine contradiction (operator, or contradictory same-key procedures)──▶ conflicted
active/extracted ──no attributed events for T (hygiene staleness)──▶ obsolete
FORBIDDEN: promotion from unverified successes; counters fed by unattributed episodes;
`conflicted` as a low-confidence synonym; any judgment computed over the legacy
success_count/failure_count (polluted history — audit-only, §13).
```

## 7. Operational rules (write / retrieve / replay / verify / conflict / quarantine / attribution)

### 7.1 Write
- Every write stamps the full envelope. No write path may omit it (one shared helper).
- The existing gates stay and remain real: `MemoryWritePolicy` (+ echo-antibody as live
  input), `KnowledgeWritePolicy.decide` (status/confidence/trust/hype/secret rejections).
- The run-scoped learning brake (MIR-022, `LearningMode = NORMAL | NO_DURABLE_LEARNING |
  AUDIT_READ_ONLY`) is honored by **all** durable sinks on **all** paths.
- Autonomous paths never write `user_explicit` origin. Sub-agents never write directly.

### 7.2 Retrieve
- Every retrieval (LTM block, experience block, procedures, chain injection) **must consult
  D5** first: `quarantined/blocked` never surface; `restricted` surfaces only inside an
  explicitly labeled `<disputed_context>` block that the synthesizer may not cite as
  support.
- Replay records (D1=`memory_replay`) are excluded from: fast-path candidacy, experience
  aggregation, procedure distillation and attribution, re-ask quality boosting. Audit only.
- One tokenizer for all memory retrieval (MIR-007/008): a single shared module; the
  acronym question (keep/drop 2-char alphabetic tokens) is decided once, there.

### 7.3 Replay (fast-path)
Fast-path may serve a stored answer **only if** the source episode has
`verification_status == verified` **AND** `response_origin != memory_replay` **AND**
`usage_eligibility == allowed` **AND** all existing safe constraints (similarity ≥ 0.85,
no file hint, non-`:` input, no local critique, `full_answer`, `not tools_used`).
The replay it banks is: D1=`memory_replay`, D2=`not_run`, D3=`replay_echo`,
`verified_chunks=0`, `source_episode_id=<origin>`. A replay never raises any counter,
status, or trust of the source episode. (Closes MIR-041; closes the fast-path face of
MIR-002; the `answer_quality_score` influence on hygiene/re-ask is the separate remaining
MIR-002 part, P8.)

### 7.4 Verify (amending the ONE existing verifier — no second verifier)
- Citation resolution keeps exactly its current meaning (record exists & matches id).
- The **counting rule** changes: a resolved citation contributes `verified` **only if** the
  matched evidence's trust_class ∈ {`operator_approved`, `tool_observation`,
  `corroborated_source`, `external_source`-with-real-artifact (file/web/test/log/shell)}.
  A resolved citation to memory-kind evidence with trust_class `agent_derived` or
  `replay_echo` yields a new chunk verdict **`memory_supported`**, counted with the
  unverified/weak family — never `verified`. (Closes MIR-046 and the counting face of
  MIR-042; `[memory:]` to an operator `:remember` record keeps counting verified,
  consistent with the existing `[user]` baseline.)
- Chain injection (`loop.py:1305/:1331`) attaches each record's envelope to its Evidence so
  the verifier can apply the rule; injection itself filters D5 (§7.2).
- The verifier soft-fail path records `verifier_error` (never a silent quality-1.0
  success-shaped episode).

### 7.5 Conflict (closing MIR-047)
- Conflict detection runs automatically (post-write for touched claims + periodic pass),
  not only on `:conflicts`.
- On detection: D4→`conflicted`, D5→`restricted` on the involved records — **persisted**,
  not just reported. The CLI report remains as the operator-facing view.
- Resolution is operator-only (§6.2). Until resolved, conflicted records cannot serve as
  ordinary evidence — in prompts, chain, or verifier counting.

### 7.6 Quarantine
- `QUARANTINE_TAGS` remain the operator vocabulary; the enforcement point becomes D5
  (tags map to `quarantined` at read/migration). Injection-`suspicious` content (MIR-011,
  separate defect) is a future candidate for `restricted` — noted, not solved here.

### 7.7 Attribution (procedures) — **new in v2; the input to §6.4**

Two deterministic channels; only attributed episodes may touch a procedure's counters:

- **(a) Workflow-match** (the existing coincidental channel, repaired): an episode whose
  tool sequence matches the procedure's key updates the counters **bidirectionally** —
  including `failed` and unverified episodes, which today never reach the store (the
  MIR-048 dead path). The *meaningfulness* of this channel depends on the identity
  decision (§16 D-1).
- **(b) Usage attribution** (new plumbing, closes MIR-049): procedures injected into
  planning are stamped as `used_procedure_ids` on the banked episode. A used procedure
  receives a `negative_count` event when that cycle failed or its answer ended
  unverified/partially_verified — **but only if** the episode's tool sequence actually
  overlaps the procedure's workflow (an injected-but-ignored suggestion is never
  penalized). A used procedure receives **no positive credit merely for being used** —
  positive credit requires channel-(a) verified success.

## 8. Hygiene, expiration, deduplication — and consolidation retirement

- **Hygiene becomes lifecycle-scheduled, not operator-only (closes MIR-045):** a bounded
  hygiene pass (expire → dedup → episodic prune → archive → **procedure staleness**) runs
  automatically at the end of each autonomous tick and every N interactive cycles,
  flag-gated, dry-run in shadow. It honors D5, never touches `operator_approved` curated
  records (existing protection), never prunes `lesson` episodes, and logs a delta report.
  Procedure staleness = the "absence of events" axis: no attributed events for T →
  `obsolete` (§6.4).
- **Expiration** = D4→`obsolete` + archive (existing archive sidecar), never silent delete.
- **Deduplication** keeps the echo-antibody as the *write-time* input and the hygiene dedup
  as the *store-time* pass — same vocabulary, one definition of "duplicate."
- **Consolidation is RETIRED (closes MIR-044; Option A is rejected and gone):**
  `consolidate_memory` is a tally of statuses procedures already hold — there is nothing
  in it to "apply." Prescription: stop persisting `ConsolidationReport`s; compute the
  `:smart-memory` tally **on demand** (CLI loses nothing); unwire `ConsolidationStore`
  in P6a; archive existing report files (never silently delete). The lifecycle judgments
  consolidation pretended to make are now genuinely made by §6.4 (evidence) + §8 hygiene
  (time).

## 9. Path lifecycles — **Artifact 4 (integration matrix)**

### 9.1 Target matrix (per production path)

| Lifecycle stage | Interactive (REPL/--ask) | Autonomous (runtime/queue/campaign/tick/daemon) | Sub-agent |
|---|---|---|---|
| Working memory | ✅ session-scoped | ✅ **per-run instance** (new; today ❌) | ❌ (memoryless, by design) |
| Persistent retrieve (D5-filtered) | ✅ | ✅ | read-only **narrow slice as proposal input only** (future, Part D) |
| Episodic/procedural write | ✅ | ✅ **(new — closes MIR-043)**, honoring LearningMode | ❌ — results return as `subagent_result` proposals |
| Procedure feedback (attributed, §7.7) | ✅ | ✅ | n/a |
| Fast-path | ✅ under §7.3 | ❌ disabled (unattended replay is riskier; revisit per §16 D-2) | ❌ |
| Re-ask hint | ✅ | ✅ (advisory) | ❌ |
| Verify (trust-aware counting) | ✅ | ✅ | parent verifies child output |
| Conflict auto-restrict | ✅ | ✅ | n/a |
| Hygiene (incl. procedure staleness) | every N cycles | end of tick | n/a |

### 9.2 Interactive
Unchanged shape; gains: envelope stamping, D5-filtered retrieval, §7.3 fast-path gate,
trust-aware verifier, auto-conflict, scheduled hygiene, attributed procedure feedback.

### 9.3 Autonomous / daemon (closes MIR-043)
`build_agent` wiring changes so experience stores exist when `with_persistent` (explicit
`with_experience` parameter; flag-gated). Every autonomous run banks episodes
(D1=`generated`, D2 from its verifier run) and feeds attributed procedure counters under
§6.4/§7.7. Working memory: an ephemeral per-run instance (not cross-run). Fast-path off.

### 9.4 RuntimeTask ↔ memory write-back
Every `AgentLoop.run` executed for a `RuntimeTask` stamps `task_id` into the episodes it
banks; the queue report carries the banked episode ids. A `failed`/`stopped` run banks an
`outcome=failed` episode (honest negatives are learning material — and under §7.7 they now
actually reach procedures). This makes unattended work traceable memory and gives
MIR-039's eventual fix a defined write-back target.

### 9.5 Sub-agents
Unchanged runtime (memoryless, no direct writes). Their outputs enter the parent as
D1=`subagent_result`, D3=`agent_derived`, verified by the **parent's** verifier before any
knowledge write (consistent with CENTRAL_AGENT_GOVERNANCE §3 and MEMORY_FIX_PLAN Part D).

## 10. Dead-sink resolutions

| Sink | Decision (v2 — single prescription each) |
|---|---|
| Consolidation (MIR-044) | **Retire** (§8): stop persisting reports; on-demand CLI tally; archive history. *(Option A rejected by operator; removed from this contract.)* |
| Hygiene manual-only (MIR-045) | **Automate bounded**, flag-gated (§8) |
| Source registry | **Officially scoped as audit/provenance store** — not a retrieval source for answers; its claims feed knowledge records at write time only |
| Assumptions run-scoping (MIR-027) | **Keep run-scoped by design**, documented; REPL multi-turn accumulation stays the open investigation item |

## 11. MIR → contract rule map

| MIR | Closed / addressed by |
|---|---|
| MIR-002 (fast-path face) | §7.3 gate on D2; **remainder** (quality→hygiene/re-ask readers) = P8 |
| MIR-003 | §6.4 birth=`extracted`; promotion needs ≥2 D2-verified successes |
| MIR-007/008 | §7.2 single tokenizer (one decision point) |
| MIR-009 | partially: D4 `superseded/obsolete`; full temporal model reserved (§4.1), still `planned_gap` |
| MIR-022 | §7.1 LearningMode honored by all sinks/paths |
| MIR-024 | not closed here (re-ask Jaccard quality) — separate |
| MIR-027 | §10 documented decision |
| MIR-041 | §7.3 replay banking + candidacy exclusion |
| MIR-042 | §7.4 chain envelope + counting rule (injection face) |
| MIR-043 | §9.3 autonomous wiring |
| MIR-044 | §8 consolidation retirement (P6a) |
| MIR-045 | §8 scheduled hygiene (P6a) |
| MIR-046 | §7.4 trust-aware counting, `memory_supported` verdict |
| MIR-047 | §7.5 conflict enforcement |
| MIR-048 | §6.4 bidirectional counters + §7.7(a) repaired channel (P6b) |
| MIR-049 | §7.7(b) usage attribution via `used_procedure_ids` (P6b) |
| MIR-050 | §16 D-1 open decision + defined investigation (status: needs_investigation — harm not yet demonstrated) |
| MIR-038 | §15 P7 zero-delta matrix tests |
| MIR-029/030 | acknowledged; sub-agent scope stays future (Part D), contract keeps them memoryless |

## 12. Negative invariants — what the system must NEVER do

1. Never set `verified` from origin, trust_class, confidence, similarity, quality score,
   citation resolution, corroboration count, or record existence — only from a verifier run.
2. A replay never: counts as verification, increments `verified_chunks`, upgrades any
   status/trust/counter of its source, becomes a fast-path candidate, or enters learning
   aggregation (including procedure attribution).
3. A resolved `[memory:]` citation to `agent_derived`/`replay_echo` content never counts
   as `verified` support.
4. A `conflicted`, `quarantined`, or `blocked` record never enters a prompt, the chain, or
   verifier counting as ordinary evidence (`restricted` only as labeled disputed context).
5. Migration never infers D2 or D4 from legacy indirect fields (`answer_quality_score`,
   `outcome`, `tools_used`, `source_labels`, legacy `verified_chunks`, legacy procedure
   `success_count`/`failure_count`).
6. No automatic transition into `allowed`; no automatic `blocked`; no automatic permanent
   supersession/rejection (operator-only); no silent deletion — archive only.
7. Autonomous/daemon paths never write `user_explicit`; sub-agents never write durable
   memory directly; nothing bypasses LearningMode / audit-read-only brakes.
8. Trust never rises through repetition (echo/self-citation), only through new independent
   evidence.
9. No second verifier; no parallel store; no field duplicated into a competing vocabulary.
10. The verifier soft-fail never produces a success-shaped, replay-eligible record.
11. **A procedure's standing never rises from an episode that was not D2-verified; being
    *used* never counts as success; no lifecycle judgment is computed over unattributed
    aggregates or legacy polluted counters.**
12. **`conflicted` is never assigned for low evidence** — only for genuine contradiction.

## 13. Legacy migration — **Artifact 5 (sequence)**

Additive-only; old code ignores unknown JSON keys (verified: `from_dict` uses `.get`), new
code reads legacy records via defaults. **No guessing from indirect fields.**

1. **Backup**: sidecar copy of each store file (`*.pre-envelope.bak`), counts recorded.
2. **Lazy defaults on read** (code-level, no file rewrite): missing envelope ⇒
   `schema_version=0`, `verification_status=not_run`, `usage_eligibility=allowed` (unless
   `QUARANTINE_TAGS` present ⇒ `quarantined` — a *direct* field mapping, permitted),
   `response_origin` from **direct** fields only: persistent `source=user-explicit` ⇒
   `user_explicit`; `source=agent-auto` ⇒ `agent_auto`; ingestion source types ⇒
   `ingested_source`; otherwise `generated`. `trust_class` derived from that origin (§4.3
   is deterministic, hence permitted). Episodes: origin `generated`; **no** attempt to
   reconstruct which were replays — legacy `memory:<id>` labels stay advisory audit data.
3. **Registry rename**: claim `verified` → `active` (one-time, reversible, logged).
4. **Procedures (v2):** legacy `needs_review` → `extracted` (direct status mapping);
   legacy `active` → `extracted` — the conservative default: legacy standing was computed
   from the polluted success-only counters (MIR-048), so trust is **not** grandfathered.
   Legacy `success_count`/`failure_count` are preserved **read-only for audit** and
   excluded from §6.4 math; the new counters start at zero. (Same discipline as legacy
   episodes → `not_run`.)
5. **Persist-on-write**: a record gains its stored envelope the next time it is legally
   rewritten; an optional one-shot offline script can stamp all records under the same
   rules (operator-triggered, with the backup from step 1).
6. **Verification of migration**: record counts unchanged; zero records lost; sample
   assertions (every legacy episode reads as `not_run` ⇒ fast-path-ineligible — fail-before
   test #3 turning green; every legacy procedure reads as `extracted`).
7. **Rollback**: flags off ⇒ legacy behavior (old readers ignore envelope keys); backups
   restore byte-identical pre-migration files.

## 14. Write / read / verify decision matrix — **Artifact 3**

| Operation | Precondition (contract) | Effect allowed | Effect forbidden | Authority |
|---|---|---|---|---|
| `:remember` | operator input | knowledge record D1=`user_explicit`, D3=`operator_approved`, D4=`active` | setting D2 | Operator |
| Auto-write (pipeline) | `KnowledgeWritePolicy` passes; LearningMode=NORMAL | record D1=`agent_auto`, D3=`agent_derived`, D4 per pipeline (`extracted`/`active`) | D2 ≠ `not_run`; bypassing write policy | Pipeline |
| Ingest | source registered; gates pass | D1=`ingested_source`, D3 per corroboration | D2 setting | Pipeline |
| Bank episode | any completed cycle | event record; D2 from **this run's** verifier report; `verified_chunks` = report values; `used_procedure_ids` stamped | synthesised counts; success-shaped `verifier_error` | Loop+Verifier |
| Bank replay | fast-path fired (§7.3) | D1=`memory_replay`, D2=`not_run`, chunks=0, link set | `verified_chunks>0`; touching source episode | Fast-path |
| Procedure feedback | attributed episode (§7.7) | counters + auto `extracted ⇄ active` per §6.4 | setting D2/`conflicted`; counting unattributed episodes or unverified successes | Procedural feedback (under Loop) |
| Retrieve (any) | D5=`allowed` (or `restricted`→disputed block) | inject with envelope attached | surfacing quarantined/blocked; replays as experience | Loop |
| Fast-path serve | §7.3 full gate | verbatim answer + replay bank | serving `not_run`/legacy/replay/conflicted sources | Loop |
| Chain injection | retrieval rules passed | Evidence carries envelope | trust-blind uniform injection | Loop |
| Verifier counting | resolved citation | `verified` only per §7.4 trust rule; else `memory_supported`/existing verdicts | verified-by-resolution for `agent_derived`/`replay_echo` | Verifier |
| Conflict detect | contradiction found | D4→`conflicted`, D5→`restricted`, persisted | rejection/supersession/deletion | Conflict detector |
| Operator resolve | operator command | D4/D5 transitions per §6 | — | Operator |
| Hygiene | schedule/flag | expire→`obsolete`+archive, dedup, prune, procedure staleness→`obsolete` | deleting curated/`lesson`; touching D2 | Hygiene |

## 15. Phased implementation plan — **Artifact 6**

Small, separately verifiable, flag-gated steps; every phase carries its own
tests-that-fail-before, its own commit(s), and an off-switch. Master flag
`AGENT_MEMORY_LIFECYCLE = off | shadow | enforce`, plus per-integration-point sub-flags.
**A clean branch from `f317c4c` per phase; never the rejected `fix/mir-040-*` branch.**

| Phase | Content | Proof (fails before / passes after) | Closes |
|---|---|---|---|
| **P0** | Entry-point inventory (incl. `api/server.py`, campaign, tick) + flag scaffolding + envelope fields on all four record types, defaults-on-read; **shadow only, zero behavior change** | schema round-trip tests; legacy reads get `not_run`/`extracted`; full suite green | foundation |
| **P1** | Write-side D2: episodes bank real verifier verdicts; soft-fail → `verifier_error` | new episodes carry honest D2; soft-fail no longer quality-1.0-success | MIR-002 blast-radius note |
| **P2a** | Fast-path replay banking per §7.3 (commit 1) | fail-before tests #4, #5, #6 turn green | MIR-041 |
| **P2b** | Fast-path gate on D2 (commit 2) | tests #1, #2, #3 green; guard #7 stays green (re-seeded `verified`) | MIR-002 (fast-path) |
| **P3** | Verifier trust-aware counting + envelope-carrying chain injection (`memory_supported` verdict) | `test_verifier_memory_trust.py` both fail-befores green | MIR-046, MIR-042 |
| **P4** | Conflict enforcement (auto `conflicted`+`restricted`; operator resolve commands), shadow → enforce | a conflicted record stops being citable as evidence (new integration test) | MIR-047 |
| **P5** | Autonomous wiring: experience stores on the autonomous path + per-run working memory + `task_id` linkage; LearningMode across sinks | an autonomous tick banks an episode; Δ=0 under NO_DURABLE_LEARNING | MIR-043, MIR-022 |
| **P6a** | Scheduled hygiene (dry-run→enforce) incl. procedure staleness + **consolidation retirement** (stop persisting; on-demand tally; archive) | growth bounded in a synthetic long run; `:smart-memory` output equivalent; no new report files | MIR-045, MIR-044 |
| **P6b** | **Closed-loop procedure feedback**: bidirectional counters + `used_procedure_ids` attribution + §6.4 transitions; identity decision (§16 D-1) implemented | a failed attributed cycle demotes a procedure (fails before — demotion unreachable today); unverified successes do not promote | MIR-048, MIR-049, MIR-003 (rest), MIR-050 (per D-1) |
| **P7** | Legacy migration script + backups + per-mode/per-store zero-delta matrix + full integration suite over every production path | migration invariants (§13.6); MGA-09-style matrix green | MIR-038, migration |
| **P8** | Residuals: single tokenizer; remaining MIR-002 readers (hygiene/re-ask use of quality); re-ask threshold review | tokenizer differential tests; hygiene/re-ask no longer treat empty-chain 1.0 as high quality | MIR-007/008, MIR-002 (rest), MIR-024 |

Each phase ends with: targeted tests + related suite + safety review (the MIR-040 lesson:
concurrency / long runs / exhausted retries / real process behavior where applicable) —
only then a `fixed` claim, per-phase, in the registry.

## 16. Open decisions — **DECISION REQUIRED before the affected phase; nothing here is prescribed**

- **D-1 — Procedure identity (MIR-050; blocks completion of P6b).** Today's key is the
  tool sequence alone, so attributed evidence may aggregate across unrelated goals.
  Options: **(a)** composite key = tools + normalized goal-token signature; **(b)** keep
  the tool key, bucket evidence per goal-class underneath it. **Decision input (also
  resolves MIR-050's status):** a read-only measurement over the live procedural store —
  how many distinct goals/questions share one `workflow_key`, and would (a) vs (b) have
  judged them differently. Until measured, MIR-050 remains `needs_investigation` (harm
  not demonstrated), and this contract does not prejudge the option.
- **D-2 — Autonomous fast-path.** Disabled in §9.1. Revisit only after P2/P3 behavior data
  exists; re-enabling would require the same §7.3 gate plus an unattended-specific risk
  review.

---

*This contract is the single canonical M1 document (v2-draft). Amendments happen here,
versioned in the header log. Upon operator approval, implementation starts at P0 on a
clean branch from `f317c4c`. Until then: no production code, tests, or schema changes.*
