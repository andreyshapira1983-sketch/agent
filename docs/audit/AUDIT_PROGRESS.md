# Audit Progress — Deep Revision

Working tracker for the multi-stage deep revision. One row per stage. Nothing here
changes code or other documents; it records what was done, what remains, and the
limits of each check. Operator command gates every stage transition.

- **Audited commit baseline:** `main` @ `f317c4c` (`f317c4c74956fca0e2d2a6b5200ce5b38f74a31d`).
- **Source-of-truth order:** current code → wired execution paths → reproducible tests → canonical docs.

> **CURRENT STATE (2026-07-20).**
>
> **This file states no issue count.** Every number it once carried went stale,
> including the banner that claimed 50 while the registry held 53. The count
> lives in `MASTER_ISSUE_REGISTRY.md`, is *generated* from the entries
> themselves, and is verified by `python scripts/registry_tally.py --check`.
> Ask the generator, never this page.
>
> **Everything below this banner is a chronological log.** Counts and statuses
> inside it ("Registry now 42 issues", "MIR-002 stays open") were true when
> written and are kept as the record of how the revision proceeded. They are
> not claims about today, and must not be read as such.
>
> `MEMORY_LIFECYCLE_CONTRACT.md`: **v2-draft, never approved** — and now partly
> overtaken: MIR-002/041/043/046/047/048/049/051 were closed by direct fixes on
> branch `fix/mir-043-autonomous-experience-wiring`, not by executing that
> contract. See its own header note before planning any work from it.
>
> Production code: no longer unchanged. That branch carries the memory work
> from MIR-043 through MIR-047; the registry entries name the commits.

---

## Stage 1 — Documentation inventory — ✅ COMPLETE (awaiting operator review)

**Goal:** inventory every doc related to audits, known defects, fix plans, roadmap,
daemon, memory, governance, subagents, operational failure modes, self-audit,
self-improvement; classify each; check "current implementation" claims against code.

**Done:**
- Read 17 documents in full (14 in `docs/` + `docs/future/CORPORATE_MODEL.md` + root `README.md`, `AGENT_DOCTRINE.md`, `AGENTS.md`); 1 canonical file (`архитектура автономного Агента.txt`) opened but not read in full.
- Recorded per-doc: path, purpose, commit/date, contained problems/decisions, inbound/outbound references, unique info, overlaps, code contradictions, proposed status.
- **Code-verified** the load-bearing "current implementation" anchors of the audit docs (`core/smart_memory.py`, `core/confidence_gate.py`, `core/loop.py`, `core/loop_methods2.py`, `core/task_queue.py`, `tools/shell_exec.py`, `core/planner.py`, `core/loop_helpers.py`) and confirmed the presence of named regression tests.
- Output written to `docs/audit/DOCUMENT_INVENTORY.md`.

**Key result:** the audit docs are honest and well-structured, but **partially stale** —
CORE-01/04/05/07/09/10, MGA-06, OFM-010, LPF-001, and #9 are **fixed in code** with
regression tests, written *after* the docs. Still open (verified): CORE-06, CORE-08,
CORE-03 (reframed). Ambiguous: CORE-02. New `core/intent_understanding` layer is
undocumented in ROADMAP/COMMANDS_MAP.

**Verification limitations (Stage 1):**
- Code was **read**, tests were **confirmed present**, but the suite was **not run** and the **live agent was not executed**.
- Only anchors named by the audit docs were checked; the daemon PR-merge states, the subagent "verified today" claims, and the governance "IMPLEMENTED" claims were **not** re-verified.
- `архитектура автономного Агента.txt` not read in full.

**Remaining / handed to Stage 2 (do NOT start without operator command):**
- Re-verify `daemon-progress.md` PR states (esp. item 4.1 `open`).
- Re-check `SUBAGENT_LIFECYCLE.md` / `CENTRAL_AGENT_GOVERNANCE.md` "verified/IMPLEMENTED" claims.
- Re-diff `COMMANDS_MAP.md` and roadmap against current `main.py` + `core/intent_understanding`.
- Re-count `AGENT_ANATOMY.md` modules.
- Resolve CORE-02 ambiguity (`smart_memory.py:409` quality gate).
- Full read of the canonical architecture `.txt`.

---

## Stage 2 — Master issue registry — ✅ COMPLETE (awaiting operator review)

**Goal:** consolidate every problem from the audits, fix plans, live-probe logs,
roadmap, daemon tracker, and fix history into one register, re-grounded against
current code on `main` @ `f317c4c`, with canonical IDs + aliases + merge rules.

**Done:**
- Created `docs/audit/MASTER_ISSUE_REGISTRY.md` with **38 canonical issues** (`MIR-001…038` + one external-checklist row), each carrying: canonical ID, all prior aliases, title, source docs, first-documented commit/date, files/functions, symptom, root cause, related causes, production execution path, existing tests, missing tests, status, status evidence, approved fix (if documented) / alternatives (if undecided).
- **Applied merge discipline:** merged only on proven common root (12 merges, logged in the registry's "Merge decisions log"); kept prior IDs as aliases; did **not** merge on similar-symptom-only; kept the CORE-01…05 learning-loop findings as separate cross-linked issues (independent locations/fix-status).
- **Provenance tags** applied: `previously_documented`, `independently_reconfirmed`, `newly_discovered` (3 newly discovered: MIR-035 Part-B not executed, MIR-036 audit-doc staleness, MIR-037 intent_understanding doc gap).
- **Additional code grounding this stage:** CORE-02 (`procedure_from_episode` unchanged; upstream CORE-01 mitigation → `partially_fixed`); CORE-03 (`return 1.0` kept, A3 not applied → `open`); MGA-01/05, LPF-002/003/007/009/014/015/016 confirmed open in code; `intent_understanding` confirmed to be a binary conversation/action router that does **not** add no-learning/self-diagnostic intents; `data/self_improvement_issues.jsonl` inspected (40 auto-failure records, not the memory-fix defects → MIR-035).

**Status distribution:** 8 `code_fixed_needs_runtime_verification`, 12 `open`,
1 `partially_fixed`, 8 `planned_gap`, 5 `needs_investigation`, 2 `stale_finding`,
1 process-`open`. **No item marked plain `fixed`** — suite not executed this stage.

**Verification limitations (Stage 2):**
- Evidence = code reading + regression-test-file existence. **Test suite not run; live agent not executed.** Hence `code_fixed_needs_runtime_verification`, never `fixed`.
- Fix commits not archaeologically enumerated; evidence cites current `file:line` + guarding test.
- Stage-1 carry-overs (daemon PR states, SUBAGENT/GOVERNANCE "IMPLEMENTED" claims, COMMANDS_MAP re-diff, anatomy module recount, full read of the architecture `.txt`) remain **not** re-verified.

**Remaining / candidate Stage 3 (do NOT start without operator command):**
- Runtime verification pass: run the named regression tests + drive the production path to upgrade `code_fixed_needs_runtime_verification` → `fixed` (or `regression`).
- Resolve `needs_investigation` items (MIR-019, 025, 027, 028, 032, 033).
- Decide the undecided design choices (MIR-002 quality floor, MIR-006 success criterion, MIR-010 per-command exit semantics, MIR-015/016 gate enforcement).
- Refresh the stale audit-doc ledgers (MIR-036/037) — under explicit command; do not delete history-of-regression docs.

---

## Stage 3 — Deep verification by subsystem — 🔄 IN PROGRESS (one group done)

Deep per-issue verification, split by subsystem to bound cost. Each group: check
current `main`, trace the full execution path, find all callers/consumers, check
alternative paths, compare with docs+commits, check tests, reproduce where
possible, update status+evidence in the registry. **No code fixes.**

### Group 3.1 — Task Queue / Scheduler / Runtime lifecycle — ✅ COMPLETE (awaiting operator review)

**Scope:** MIR-017, MIR-026, MIR-033, MIR-034 (+ 2 newly discovered: MIR-039, MIR-040).

**Done (evidence written into MASTER_ISSUE_REGISTRY.md):**
- **MIR-017** (retry backoff) → **`fixed`** (⬆ from code_fixed_needs_runtime_verification). Reproduced in isolation (tmp, no durable mutation): `max_attempts=3` → 30s→60s→terminal `failed`; `max_attempts=1` → terminal after one fail; task not eligible in `pending()` until the delay elapses. Reached via `run_task_queue`; **not** via the `agent_tick` cron path (which never calls `mark_failed`).
- **MIR-033** (consumer in production loop) → refined `needs_investigation`. **Two divergent consumers**: `autonomous_runtime.run_task_queue` (CLI `:task-run` + campaign, proper lifecycle) and `agent_tick.py` (cron fallback, unconditional `mark_done`). **No always-on daemon drain** — `app/daemon.py` is a generic wake loop, not composed to the queue (daemon-progress 4.2–7.4 not started). No backlog alarm.
- **MIR-034** (dead-runner heartbeat) → confirmed `planned_gap`. Heartbeat written on every `agent_tick` path (start/error/complete); consumed **advisory-only** via `best_next_action`; no self-heal, no active alarm. Tied to the `agent_tick` path only.
- **MIR-026** (goal/plan finalizer) → durable `RuntimeTaskStore` **API** lifecycle confirmed well-formed; the consumer misuse split out as MIR-039; the ephemeral in-run Goal/Plan finalizer (planner subsystem) not re-verified this group.
- **MIR-039** (newly_discovered, `open`) — `agent_tick` marks tasks `done` regardless of run outcome; a raising `runtime.run()` leaves the task permanently `running` (outer handler never updates status).
- **MIR-040** (newly_discovered, `planned_gap`) — `recover_stuck` is defined + unit-tested but **has zero production callers**; stuck `running` tasks are never auto-recovered. Ties to daemon 4.2 "Recovery on start" (not started). OFM doc's "verified control present" for `recover_stuck` is misleading.

**Verification limitations (Group 3.1):**
- MIR-017 reproduced at the `mark_failed`/`pending` API level; the **live cron tick and the daemon were not executed** end-to-end (agent_tick/daemon paths verified by code reading only).
- Only the Task Queue / Scheduler / Runtime-lifecycle subsystem was examined. Scheduler enqueue (`SchedulerStore.tick`) was read but has no open registry issue; not exhaustively audited.
- Durable stores were not mutated (reproduction used a throwaway tmp queue).

**Remaining Stage-3 groups (do NOT start without operator command):**
- 3.2 Daemon & Windows Service · 3.3 Memory & learning · 3.4 Planner & step dependencies · 3.5 Tools/cache/policy · 3.6 API · 3.7 CI & supply chain · 3.8 Documentation drift.

### Fix pass — MIR-040 (recover_stuck wiring) — ✅ COMPLETE (isolated commit)

**Branch:** `fix/mir-040-wire-recover-stuck` (off `main` @ `f317c4c`).

**Workflow followed (12 steps):** re-showed root chain → traced both consumer paths → found callers/consumers → checked alternative drain paths → compared with OFM doc → checked existing tests → wrote a **fail-before** regression test (proved `assert 'running' == 'done'` failed on old code) → fixed the root (one line: `run_task_queue` calls `recover_stuck()` before draining) → ran targeted test (green) → ran related suite (**177 passed**) → repo-wide search for the old path → verified the `:task-run` production entry point → updated MASTER_ISSUE_REGISTRY.md (MIR-040 → `fixed`, tally, cross-refs).

**Files changed (isolated):** `core/autonomous_runtime.py` (+recovery call), `tests/test_autonomous_runtime.py` (+regression test). No other code touched; no unrelated issue fixed.

**Status:** MIR-040 → **`fixed`** for the AutonomousRuntime consumer (full confirmation: fail-before + pass-after + no regression + production entry point verified). **Residual (out of scope, tracked under MIR-039):** the `agent_tick` cron consumer still lacks recovery.

**Limitation:** verified by driving the real `run_task_queue` method end-to-end (FakeLLM agent, real TaskQueueStore); the **live `:task-run` CLI and cron process were not executed** with a real LLM.

### ⚠️ Correction — MIR-040 `fixed` was WRONG; downgraded to `open`

An operator safety review flagged that the fix pass proved recovery *happens* but not that it is *safe*. Re-verified against code + reproduced in isolation:
- **Double execution (regression risk):** `recover_stuck` staleness is on `updated_at`, which is set once at `mark_running` and **never refreshed during a run** (no heartbeat). A *live* long task (>timeout) is indistinguishable from a crashed one → a second consumer's `recover_stuck` resets it to `pending` → the same task runs twice. Consumer entry points hold **no single-instance lock** (`app/single_instance.py` exists but is not acquired), so concurrent consumers are real. Reproduced: a 40-min `running` task reset to `pending`.
- **`max_attempts` bypass:** `recover_stuck` resets to `pending` without touching `attempts`; a task stuck at the cap is resurrected and runs one time past `max_attempts`. Reproduced.
- **Real crash not exercised:** both the test and repros *simulate* a crash (`mark_running` + backdated `updated_at`).

**Corrected status:** MIR-040 → **`open`**. The committed one-liner (`02a8a58`) is **unsafe and must not be merged**. The correct fix is startup-only recovery under the single-instance lock (`daemon-progress.md` item 4.2, not started) and/or a task heartbeat / liveness check, plus `attempts` handling — an architectural change, not a `run_task_queue` one-liner. Registry (MIR-040, tally, MIR-033 cross-ref) updated to reflect the downgrade.

**Resolution (operator-approved revert):** `02a8a58` was reverted by `e8d330d` on `fix/mir-040-wire-recover-stuck`. `core/autonomous_runtime.py` and `tests/test_autonomous_runtime.py` now match `main` (no diff); `recover_stuck` has no production caller again (safe-but-stranded behavior restored); 44 task_queue/autonomous_runtime tests pass. MIR-040 remains `open`, deferred to the guarded daemon-4.2 fix. No net code change landed from this fix pass.

**Lesson:** a passing fail-before/pass-after test proves the change *does something*, not that it is *safe under concurrency / long runs / exhausted retries / real crashes*. Safety review must precede a `fixed` label for anything touching shared runtime state.

### Group 3.3 — Memory & learning — ✅ COMPLETE (read-only, no code changes)

**Scope:** MIR-001, MIR-002, MIR-003, MIR-004, MIR-005, MIR-006, MIR-007, MIR-008, MIR-009, MIR-022, MIR-038. Read-only deep verification; reproduced behaviour in isolation; ran targeted memory tests (41 green on `main`). **No code touched.**

**Status changes (evidence in MASTER_ISSUE_REGISTRY.md):**
- **MIR-001** (episode outcome) → **`fixed`** ⬆. Reproduced: `verified=1,unverified=10 → 'partial'`; general-knowledge `(0,0,0) → 'success'` preserved. Pure function, no concurrency risk.
- **MIR-004** (fabricated-citation credit) → **`fixed`** ⬆. Reproduced (with `total_chunks`): fabricated cites → `0.0` (was 0.5); real verified → `1.0`.
- **MIR-005** (experience-retrieval filter) → **`fixed`** ⬆. Confirmed the `outcome=='success' or lesson` filter + targeted test; read-only retrieval predicate, safe.
- **MIR-007** (episodic tokenizer) → **`partially_fixed`** ⬇ (was code_fixed). Numbers now kept (`17`,`23`), but short **acronyms `AI`/`ML`/`os` are still dropped** — the fix covered only the digit half of CORE-10.
- **MIR-003** (procedure activation) → stays `partially_fixed`, reproduction added (bad episode → no procedure; genuine success still born `active`).
- **MIR-002** (quality 1.0 on empty chain) → stays `open` but **re-graded from "design decision" to "potential defect (downstream-confirmed)"**. Tracing `answer_quality_score` readers showed the 1.0 is **not inert**: it satisfies the `episodic_fast_path` gate (`loop.py:764`, needs `quality>=0.70` + `not tools_used`), so a near-identical later question **replays an ungrounded answer verbatim, skips verify, and re-banks it `verified_chunks=1`**. Reproduced with a deliberately *wrong* ungrounded answer. Also influences hygiene pruning and re-ask detection. A score is only a "design choice" if nothing downstream acts on it — here something does.
- **MIR-006/008/009/022/038** → re-confirmed unchanged (`open`/`open`/`planned_gap`/`planned_gap`/`planned_gap`).

**Net:** 3 memory items upgraded to `fixed`; 1 downgraded to `partially_fixed` (acronyms still dropped); **1 (MIR-002) re-graded to a potential defect** after a downstream trace.

**Deep-dive addendum — episodic fast-path (`core/loop.py:744-796`):** full read + reproduction of the fast-path revealed a **new issue, MIR-041** (`open`, newly_discovered): when a past episode matches at `similarity>=0.85` with `quality>=0.70` and no tools, the loop returns the cached answer verbatim — **skipping the entire verify pipeline** — and re-banks it as `verified_chunks=1`. The gate never checks the original verifier verdict (so, via MIR-002, an ungrounded/wrong answer is replay-eligible), and the re-banked replay is itself fast-path-eligible again → a **self-reinforcing loop** that launders "matches memory" into "verified." MIR-002 and MIR-041 are independent but compounding; the recommended fix addresses both (don't replay the unverified; don't re-bank a replay as verified).

### Group 3.3 — read-only trace COMPLETED (operator-directed full-subsystem pass)

Operator chose fix **Variant 2+3** and pinned the target semantics (6 points) into the registry as the "Fix-design contract"; **no code yet** — finish the whole-subsystem trace first. Completed:

- **Fix-design contract recorded** (registry, under MIR-041): fast-path allowed only with explicit proof the original answer passed Verify / rested on valid evidence; verification must NOT be inferred from `answer_quality_score` / `outcome` / absence of `tools_used` / fast-path-synthesised `verified_chunks`; replays stored with `source=episodic_fast_path/memory_replay`, never `verified_chunks=1`, never a new verification, never raising trust in the source episode; MIR-002 & MIR-041 stay separate; no arbitrary `quality→0.3`.

- **Complete reader list of the affected episode fields** (so a provenance change breaks nothing):
  - `answer_quality_score`: `episodic_hygiene.py:77/103/125` (pruning), `loop.py:764` (fast-path gate ⚠), `loop.py:773` (log), `loop_methods2.py:246/284/353` (re-ask text + logs), `smart_memory.py:409` (re-ask threshold `<0.5`). *(Not stored in JSONL — recomputed on load.)*
  - `episode.outcome`: `episodic_hygiene.py:80/82/126` (pruning), `loop_methods2.py:188` (experience filter — MIR-005), `:268/283/352/423` (re-ask/log/tally), `smart_memory.py:223/224` (procedure success/fail counts), `:579` (procedure gate), `:655` (consolidation text).
  - `episode.tools_used`: `loop.py:766` (fast-path gate ⚠), `smart_memory.py:579/581/582/585/592` (procedure build), plus logs/serialisation.
  - `episode.verified_chunks`: `smart_memory.py:161/535` (→ feeds `_compute_quality_score`), `loop_methods2.py:356` (log). *(Report-side `verified_chunks` in confidence_gate/low_evidence/verifier_models is a DIFFERENT object — the verifier report, not the episode.)*

- **Every synthetic-verified path found:** only **one** episode is written with `verified_chunks=1` without a real verifier report — the **fast-path** (`loop.py:783`). The normal respond path (`:2097`) uses `verification.verified_chunks` (honest). Refusal path (`:844`) uses `unverified=1` (honest). So "don't launder replays as verified" is localised to one site.

- **EpisodeRecord schema:** has `source_labels` (already carries `memory:<id>` on replays — a natural home for a `memory_replay` marker) but **no explicit "was this Verify-passed?" field** — today verification is only inferable from the very signals rule #2 forbids. → a minimal provenance addition (one explicit field) is required.

- **New trace findings:**
  - **MIR-042** (`needs_investigation`, newly_discovered): persistent + working memory records are injected into the verifier's provenance chain **uniformly, with no trust-class filter** (`loop.py:1305-1347`), so a `[memory:]` citation resolves as matched evidence regardless of the record's own verification. Semantic-side analogue of MIR-041; the "does a matched memory citation count as verified?" question is **deferred to group 3.4 (verifier)**.
  - **Verifier soft-fail** (`loop.py:1533`): a verifier *crash* records a `verified=0,unverified=0,fully_unverified=False` → `quality=1.0` episode — another ungrounded-1.0 source feeding the fast-path (noted under MIR-002).

**Stage-3.3 status:** read-only trace **complete**. Registry now **42 issues** (added MIR-041, MIR-042; MIR-002 re-graded; MIR-007 → partially_fixed; MIR-001/004/005 → fixed).

**Next (post-trace, before any code):** (1) provenance scheme — **corrected by operator to TWO axes** (`verification_status` × `response_origin`), recorded in the registry Fix-design contract; (2) reader list — done (above); (3) fail-before tests — **DONE** (below); (4) then code.

### Fail-before tests written & demonstrated (operator-approved; NO production code changed)

`tests/test_fast_path_verification_provenance.py` — 7 behavioural tests driving `AgentLoop.run` and observing whether `episodic_fast_path` fired / what was banked (not assertions on not-yet-existing fields). On current `main`: **6 fail for their intended reasons, 1 (protective) passes.**
- MIR-002 #1 ungrounded quality=1.0 replayed · #2 verifier-error episode replayed · #3 legacy (no-provenance) episode replayed → all fail (fast-path fired).
- MIR-041 #4 replay banked `verified_chunks=1` · #5 replay-shaped record used as fast-path source · #6 self-reinforcing chain (4 verified-success episodes after 3 asks) → all fail.
- #7 GUARD: a genuinely-verified episode still fast-paths → passes (must keep passing after the fix; its seeding will set `verification_status=verified` once the field lands).

**Proposed minimal file set for the fix (NOT yet applied — awaiting operator go-ahead; MIR-002 & MIR-041 as SEPARATE commits):**
- `core/smart_memory.py`: add `verification_status` + `response_origin` fields to `EpisodeRecord` (+ optional `source_episode_id`); `to_dict`/`from_dict` with **legacy default `verification_status=not_run`, no guessing**; new params on `episode_from_agent_cycle`.
- `core/loop_methods2.py`: thread `verification_status` / `response_origin` (+ link) through `_record_experience_memory` (the single episode-creation funnel).
- `core/loop.py`: (a) fast-path gate keyed on `verification_status=="verified"` **and** `response_origin!="memory_replay"` instead of `answer_quality_score>=0.70` [MIR-002 fast-path part]; (b) fast-path re-bank as `response_origin=memory_replay, verification_status=not_run, verified_chunks=0` + source link [MIR-041]; (c) set `verification_status` from the real verifier verdict on the normal respond path (`:2091`), `verifier_error` on the soft-fail (`:1533`), `not_run`/`unverified` on refusal (`:838`).
- `tests/test_fast_path_verification_provenance.py`: after the fix, update test #7 seeding to `verification_status=verified`.

**Explicitly NOT in this set (separate, still-open part of MIR-002):** the influence of `quality=1.0` on `episodic_hygiene` pruning and re-ask detection — a distinct fix.

**Verification limitations (Group 3.3) — honest scope:** reproductions were isolated function-level / targeted-test level; the **full live agent tick was not run end-to-end**. Correct framing: *in the verified scope, no critical defect was found beyond the ones already registered, and MIR-002 was upgraded to a potential defect* — this does **not** prove full end-to-end coverage of the whole memory subsystem. The `fixed` upgrades (MIR-001/004/005) are justified only because they are pure/read-only functions with no shared-state/concurrency hazard (contrast MIR-040). Subagent memory scope (MIR-029) belongs to the subagent group, not verified here.

---

## M0 — Memory Map (read-only foundation for the lifecycle reconstruction) — ✅ COMPLETE

Operator authorized M0 (finish the whole-memory read-only map + dead-sink cross-map → a "Memory Map" doc) as Phase 0 of rebuilding memory as one coherent lifecycle. **No code changed.** Delivered `docs/audit/MEMORY_MAP.md`.

**Key results:**
- **Store census (7 stores + chain):** working, persistent/semantic, episodic, procedural, consolidation, user-profile, assumptions, source-registry — each mapped write→read→"influences a decision?".
- **Biggest integration gap (new — MIR-043):** episodic/procedural/consolidation stores are created only under `with_memory=True` (`app/bootstrap.py:143-158`); the **autonomous/daemon path runs `with_memory=False`** → it records no episodes, builds no procedures, has no working memory. All experience learning / fast-path / re-ask exist **only** in the interactive path. Sub-agents are fully memoryless.
- **Dead sinks (new):** consolidation is written every cycle but read only by CLI status (MIR-044); hygiene is manual-only, never automatic (MIR-045); source-registry is written per-cycle but not re-read into answering; assumptions are run-scoped.
- **MIR-042 resolved:** the verifier DOES count a matched `[memory:]` citation as `verified` (`verifier_core.py:114-116`); records enter the chain with no trust filter (`loop.py:1305`); **`knowledge_auto_write` defaults to True in production** (`bootstrap.py:250`) → the semantic laundering loop is active by default (throttled by write-policy/echo-antibody). Status `needs_investigation` → `open`.
- **Duplicate/overlap mechanisms** for M1 to unify: two tokenizers (MIR-008/007), `verified_chunks` overloaded (episode vs verifier report), three similarity gates on one episodic store, quality-vs-verification conflation (MIR-002).

**Registry now 45 issues** (added MIR-043/044/045; MIR-042 re-graded). §7 of the Memory Map lists the inputs handed to **M1** (design the single lifecycle contract).

**M0 verification limits:** code-level census + reading; no live multi-turn run (interactive+autonomous) to confirm autonomous memory-blindness end-to-end or measure auto-write volume.

### M0 boundary threads — ✅ COMPLETE (operator-directed, before M1)

Two remaining read-only traces closed; Memory Map extended (§9-§13); new defects registered separately from MIR-042. **No production code changed.**

- **Thread A — verifier trust (group 3.4):** focused test `tests/test_verifier_memory_trust.py` (2 fail-before, 2 proof) proves a matched `[memory:<id>]` citation counts `verified` by **resolution, not content**, and the verifier does **not** distinguish trust (user-approved = agent-auto = working-artifact). → **MIR-046** (verdict side; kept separate from MIR-042).
- **Thread B — ingestion / knowledge pipeline / conflict_review:** mapped all four persistent-write paths + their trust; confirmed `KnowledgeWritePolicy.decide` really rejects unverified/conflicted/low-conf/untrusted/hype/secret (write side well-guarded), claim "verified" = source-corroboration (≠ answer verifier), echo-antibody is a real input to the write decision, and **conflict_review only reports** (no status change/quarantine) → a conflicted record stays citable as verified. → **MIR-047**.
- **Deliverables added to `MEMORY_MAP.md`:** §11 enumerates the five fragmented trust vocabularies + de-facto response origins; §12 is the **write → read → "counts as verified" matrix** (one-line: anything that resolves in the chain counts verified unless an operator manually quarantined it).

**Registry now 47 issues** (added MIR-046, MIR-047). **M0 fully complete.**

## M1 — Memory Lifecycle Contract — ✅ DESIGN WRITTEN, ⛔ AWAITING OPERATOR APPROVAL

Created the single canonical design doc `docs/audit/MEMORY_LIFECYCLE_CONTRACT.md` (operator-corrected model: **six independent dimensions** — `response_origin` / `verification_status` / `trust_class` / `claim_status` / `usage_eligibility` / confidence-weight — NOT one merged trust_class; the five existing vocabularies are kept as inputs, not replaced).

Contains all required parts: master invariant (citation resolution ≠ content verification; origin/trust/confidence/existence can never yield `verified`); canonical envelope schema for all four stores (Artifact 1); state-transition tables + authority table (Artifact 2, §5-6); write/read/verify decision matrix (Artifact 3, §14); per-path integration matrix incl. RuntimeTask↔write-back linkage (Artifact 4, §9); dead-sink resolutions (consolidation Option A/B decision pending, hygiene automation, source-registry scoped as audit); MIR→rule map (§11: closes 041/042/043/046/047, partial 002/003/007/008/009/022/038/044/045); 10 negative invariants (§12); additive legacy migration with no status inference from indirect fields + rollback (Artifact 5, §13); phased plan P0-P8, flag-gated shadow-first, clean branches from `f317c4c` (Artifact 6, §15).

Registry's earlier "Fix-design contract" section marked superseded-as-canonical (points to the M1 doc). **No production code, tests, or schemas changed. Implementation blocked until operator approves the contract (and picks consolidation Option A vs B).**

### M1 verdict (operator, 2026-07-19) — v1-draft NOT approved; root gap found and registered

- **Option A rejected** — and correctly: re-reading `consolidate_memory` (smart_memory.py:607-609) showed the report is a pure tally of statuses procedures already hold; "applying its findings" would be a tautology, a cosmetic consumer for a dead sink.
- **"B-enhanced" ruled right-direction but not root.** Follow-up code trace found why — the procedural lifecycle has **no closed feedback loop**:
  - **MIR-048** (new, `open`): non-success episodes never reach procedures (`procedure_from_episode` returns None before `with_episode`); `failure_count` increment is dead in production; confidence is a one-way ratchet from 0.667; demotion via outcomes unreachable.
  - **MIR-049** (new, `open`): no usage→outcome attribution — `_last_procedure_records` is write-only; episodes carry no `used_procedure_ids`.
  - **MIR-050** (new, `open`): procedure identity = tool sequence only; evidence aggregates across unrelated goals (design fork on the fix: goal-class key vs sub-buckets).
- Registry now **50 issues**. `MEMORY_LIFECYCLE_CONTRACT.md` header updated to "v1-draft — NOT APPROVED" with the amendment log (revision will happen in the same single doc). `MEMORY_MAP.md` §14 corrects §1's "procedural influences decisions: Yes" (retrieval yes; write-back loop broken).
- **Next (awaiting operator):** revise M1 §6.4/§8 around a closed-loop procedural lifecycle — attributed (`used_procedure_ids`), verification-keyed (D2, not `outcome`), **bidirectional** (verified-success promotes; attributed failure/unverified demotes), meaningful identity (fork to decide), plus time-decay in hygiene and consolidation retirement. No code until the revised contract is approved.

## Documentation reconciliation — 2026-07-19 (operator critique, 9 points) — ✅ DONE

The operator found the document set self-contradictory after the M1 verdict. Point-by-point disposition:

1. **Header rejected Option A / (2) body still prescribed it** → contract fully rewritten as **v2-draft**: Option A removed from §5/§8/§10/§11/§14/§15; consolidation **retirement** is the single prescription; header carries a version log recording the v1 faults.
2. *(see 1)*
3. **`conflicted` conflated with `needs_review`** → fixed contract-wide: `conflicted` = genuine contradiction only (§2-D4, §6.2, §6.4, invariant #12); evidence-degradation demotes to `extracted`; legacy `needs_review` migrates → `extracted` (§13.4).
4. **Closed-loop design not actually written** → now normative text: §6.4 (bidirectional counters, reachable demotion, thresholds) + §7.7 (two attribution channels incl. `used_procedure_ids`, no-credit-for-mere-use) + phases P6a/P6b.
5. **Registry carried stale Stage-2 limitations** → dated Update note appended to the Stage-2 limitations block: per-issue Status/Evidence lines are authoritative; later passes legitimately upgraded MIR-017/001/004/005 to `fixed`.
6. **47 vs 50 mismatch** → registry tally marked authoritative ("50 total"); CURRENT STATE banner added at the top of this tracker; MEMORY_MAP footer no longer embeds a MIR list as if current.
7. **MIR-050 claimed as proven defect before harm was proven** → downgraded to `needs_investigation` (mechanism code-verified; harm not demonstrated — same discipline as MIR-008/CORE-11), with a defined read-only measurement that doubles as the §16 D-1 decision input. Tally moved accordingly (open 23, needs_investigation 7).
8. **Accepted M0 map silently mutated** → §14 relabeled as an explicit **POST-M0 ADDENDUM** with a process note (accepted baseline = §1–§13; addendum ≠ accepted text).
9. **Four governing documents were changed while the final message read as analysis-only** → acknowledged as a process fault. Rule adopted going forward: **(a)** completed/accepted artifacts receive only labeled, dated addenda — never silent edits; **(b)** a rejected contract is revised as one coherent version bump, never patched piecemeal (no header/body divergence); **(c)** every governing-document edit is listed explicitly and prominently in the turn's final message, separate from analysis findings.

Contract v2-draft now awaits operator approval; §16 D-1 (procedure identity) and D-2 (autonomous fast-path) are the only open decisions, and neither is phrased as a prescription. No production code touched.

## Change log
- Stage 1 completed at baseline `f317c4c`; created `docs/audit/DOCUMENT_INVENTORY.md` and this tracker. No source code or pre-existing document modified.
- Stage 2 completed at baseline `f317c4c`; created `docs/audit/MASTER_ISSUE_REGISTRY.md`. No source code or pre-existing document modified; nothing fixed or deleted.
- Stage 3 group 3.1 (Task Queue / Scheduler / Runtime lifecycle) completed at baseline `f317c4c`; updated MASTER_ISSUE_REGISTRY.md (MIR-017→fixed, refined MIR-026/033/034, added MIR-039/040). Reproduction ran in a throwaway tmp queue; no durable store, source file, or other document modified.
- M0 (Memory Map) completed at baseline `f317c4c`; created `docs/audit/MEMORY_MAP.md`, added MIR-043/044/045, re-graded MIR-042. Read-only; no source code changed. (Untracked audit files remain in the working tree; not committed to the `fix/mir-040-*` branch.)
- 2026-07-19 documentation reconciliation: contract rewritten as v2-draft (Option A purged, closed-loop §6.4/§7.7 written, `conflicted`≠`needs_review`, §16 open decisions isolated); MIR-050 → needs_investigation; Stage-2 limitation block updated; tally 50 marked authoritative; MEMORY_MAP §14 relabeled POST-M0 ADDENDUM. Four working documents edited (contract, registry, memory map, this tracker) — listed here per the new disclosure rule. No production code touched.
