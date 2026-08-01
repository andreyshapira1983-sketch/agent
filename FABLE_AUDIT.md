# FABLE_AUDIT — forensic audit of recurring systemic defects

Status: PHASE 1 complete. Cluster 1 MERGED (PR #200 → `189f9bd`). Cluster 1b
MERGED (PR #201 → `9fd2800`); the live `--apply` still awaits the operator.
Cluster 2 (ROOT B) patched and proven on branch
`fix/root-b-memory-in-evidence-budget` — not reviewed, not merged.
Worktree: clusters 1/1b were built in
`copilot-worktrees/agent/andreyshapira1983-sketch-fantastic-memory`; cluster 2
was built on a branch inside the operator's own checkout, which was clean at
branch time (§6's "never commit from there" note assumed a dirty checkout).
Live data read (READ-ONLY) from the operator's checkout: `data/*.jsonl`, `logs/run_*.jsonl`.

Method: every symptom below was checked against implementation paths and real
traces. Documentation was treated as claim, not proof. "Confirmed" means the
code path AND a durable trace/store artifact both show the behaviour.

---

## 1. Verified symptoms

### S1 — the agent re-calls the LLM to answer why it called the LLM  — CONFIRMED
- Trace: `logs/run_ab3f4bb6724763987b4c4331fcb220a3.jsonl` (2026-07-31 01:04–01:08).
  - ev[19] `interpret`: goal = «Почему ты вызываешь LLM? …» (mojibake in trace, cp1251 console).
  - ev[7], ev[25] `model_call_start` role=planner; ev[51] role=synthesizer — 3 LLM calls total.
- Code: `core/loop.py:874-882` classifies the turn (`is_self_analysis_turn`,
  `core/evidence_classes.py:175`) — but the ONLY effects are: log an event,
  admit the last 3 dialogue turns as evidence (`loop.py:1585-1614`), and suppress
  nothing. There is no introspective path that answers from the agent's own
  trace/log without a fresh planner+synthesizer round-trip.
- Note: this is a design gap, not a crash. The loop has exactly one shape:
  plan(LLM) → tools → synthesize(LLM).

### S2 — current trace loses to old logs and persistent memory — CONFIRMED
- Code: `core/loop.py:4077-4100` — only `artifacts` (fresh tool outputs) pass
  through `apply_total_budget` (`core/evidence_budget.py:216-273`), which trims
  the LARGEST block first. Freshly read source files are almost always the
  largest block.
- Meanwhile `persistent_block` / `experience_block` are injected OUTSIDE the
  budget (`loop.py:915-916`, `991-993`, `3932-3966` synthesizer path) — memory
  is structurally untrimmable, fresh evidence is structurally first to go.
- Trace: ev[48] `evidence_budget_trim` labels=["read_logs:latest"],
  total 31 972 chars — the fresh read was cut; memory blocks were not.
- Consequence observed 2026-07-30 (user-documented): agent quoted memory record
  `mem_528f46c99825fb423c16396077d866fe` ("Bug fixed in core/loop_methods2.py…")
  while the fixed code itself did not survive the trim → reported an already
  -fixed bug as current.

### S3 — reasoning_action_mismatch is logged, action executes anyway — CONFIRMED
- Code: `core/loop.py:1281-1298`. `check_reasoning_actions(...)` → if mismatch:
  `self.log.log("reasoning_action_mismatch", ...)`. Then execution proceeds
  unconditionally to `_build_plan` (`:1298`). The module itself declares
  "observational only" (`core/reasoning_action_check.py:13-14`).
- Trace: ev[30] mismatch `unjustified_actions=["read_logs"]` → ev[37] `tool_call`
  read_logs executed anyway.
- Assessment: by design (MAST FM-2.6 shadow mode). It is a *sensor without an
  actuator*. Not a bug per se; a gap only if a stricter mode was claimed.

### S4 — diagnostic queries ARE written into durable memory — CONFIRMED
- Same diagnostic run (`run_ab3f4bb…`, a "why did you call the LLM" question)
  produced 5 × `persistent_memory_write` (ev[41..45]): contents are literally
  log lines re-stated as facts, e.g. «strategy_classified: 2026-07-31T01:04:43…
  Source: log:log_event:run_ab3f4bb…:36», tags `["fact","knowledge",
  "source-backed","log", claim:*]`, Confidence 0.85.
- Store check: `data/persistent_memory.jsonl` now holds 10 records with
  `Source: log:` (scan 2026-07-31).
- Gate analysis (`core/knowledge_pipeline.py`):
  - `KnowledgeWritePolicy.decide` (`:390-432`) rejects source types
    `{"forum","unknown","tool_output"}` (`:427`) — but ACCEPTS `log`,
    `test_result`, `memory`, `file`.
  - The SAME module already encodes the doctrine that log/tool_output/
    test_result/memory are "observations of one moment, not standing claims"
    (`_NON_ASSERTING_SOURCE_TYPES`, `:269`, MIR-054) — but that set is consulted
    ONLY by conflict detection (`:300`), not by the write gate.
  - Episodic banking (`loop.py:2592`) additionally banks EVERY completed cycle
    (episode sink), including diagnostic ones; only usage_eligibility is
    restricted (`usage_eligible=False` observed in the trace, ev[~134 of the
    user's larger session]). Episodic banking is by-design (records "what
    happened"), so the defect is specifically the *semantic knowledge* writes.

### S5 — architecture readiness > coding readiness — CONFIRMED (two meanings of "ready")
- Trace `logs/run_80f543a85ad2a6eeaae79614eff3420a.jsonl` (2026-07-31 11:28):
  - `architecture_audit`: ready_for_multi_agent_execution=True, 18/18 present,
    priority_gaps=[].
  - `operator_programming_readiness`: status="ready_for_read_only_programming_planning"
    (i.e. NOT ready to actually code).
- Code: `core/architecture_audit.py:81-98` — "ready" = all checks "present",
  and checks are file-existence + test-file-existence probes (`_build_checks`).
  `app/operator_status.py:410-460` — coding readiness = tool registry probes.
  Neither measures demonstrated capability (e.g. a passed end-to-end repair
  drill); they measure artifact presence. Architecture says "everything built",
  coding says "may only plan read-only" — both true, but they use one word
  "ready" for two different modalities, which reads as a contradiction and
  inflates perceived readiness.

### S6 — persistent memory contains legacy code/test/log fragments — CONFIRMED
- Store scan (`data/persistent_memory.jsonl`, 814 records):
  - 800/814 sourced `file:*`; 776 of them created 2026-07-25 in one ingestion
    wave; examples are raw `assert …` lines from tests stored as
    "fact/knowledge/source-backed", Confidence 0.85 (26 records match
    code/test/log regex).
  - 0/814 records carry any machine-readable status field.
  - 2 records state "Bug fixed …" in prose (incl. `mem_528f46c…`); nothing marks
    them as historical-fix records, so retrieval can quote them as current.
- Code: the writer was already hardened AFTER the wave — `ClaimExtractor.
  _accept_sentence` now rejects code fragments (`knowledge_pipeline.py:183`,
  commit `e1232e0` "prevent-project-ingest-memory-pollution"), and docstring
  admits these were "file chunks that flooded memory as distractors".
  But the store was never migrated: pre-filter records persist and keep feeding
  retrieval (S2 makes them beat fresh evidence).

### S7 — self-repair fails mid full programming cycle — CONFIRMED (structural)
- `core/repair_proposal.py`:
  - `:249-254` refuses when baseline tests are green (`no_failing_tests`) —
    so "write a fail-before test, then fix" is impossible for the agent itself;
    it can only repair what already breaks the suite.
  - `:303-321` refuses any target whose content exceeds one context window —
    `core/loop.py` (194 KB) and every large module are structurally unrepairable;
    proposal must contain the WHOLE replacement file.
- `core/self_repair.py:1-12`: controller "deliberately does not invent patches";
  envelope = diagnose→diff→approval→write→tests→rollback. Safe, but the
  generative half (produce a correct proposal for a real bug in a big codebase)
  is the part that fails mid-cycle. Analysis/proposal for small files: works.
  Full programming cycle (new test + multi-file change): not implemented.
- This matches the runtime evidence the operator observed: investigation runs
  produce diagnosis + honest unverified-markers, then stop short of repair.

### S8 — model audit shows default routes, not actual per-run models — CONFIRMED
- Trace `run_ab3f4bb…`: planner first call `gpt-5.4-mini`
  (route_reason=`policy:balanced:my-current-planner`), then `gpt-5.6-terra`
  (route_reason=`complexity:standard:openai`), synthesizer `gpt-5.6-terra`.
- Audit `run_80f543a…` `model_registry_audit`: `unique_route_models=
  ["openai:gpt-5.4-mini"]`, `catalog_mode=local_config`, `live_provider_catalog=false`.
  → the audit reports CONFIGURED routes; actual usage diverged (complexity
  escalation) and the audit cannot see it.
- Schema: `ModelUsageRecord` (`core/model_usage.py:83-118`) has NO run_id/
  trace_id field → the usage ledger cannot be joined to a specific run even
  though per-run `model_call_start/end` events exist in traces.

---

## 2. Causal graph

```
ROOT A. "Knowledge" admission trusts source *type*, not assertion class
        (write gate ignores the module's own non-asserting doctrine)
   ├─ immediate: KnowledgeWritePolicy accepts `log` and `test_result` sources   (S4)
   ├─ immediate: pre-e1232e0 ingestion stored raw file lines as facts           (S6)
   ├─ immediate: no machine-readable status on records → "Bug fixed" prose
   │             is retrievable as a current-state claim                        (S6→S2 incident)
   └─ shared consequence: polluted store amplifies ROOT B at retrieval time

ROOT B. Evidence budget is one-sided: fresh artifacts are trimmable,
        memory blocks are untrimmable and outside the budget
   ├─ immediate: apply_total_budget trims largest (= fresh file/log read) first (S2)
   └─ consequence: stale memory (incl. ROOT-A pollution) outweighs current code (S2 incident)

ROOT C. Sensors without actuators / probes without capability measurement
   ├─ reasoning_action check is observational by design                          (S3)
   ├─ architecture "ready" = files present; coding "ready" = tools registered —
   │  same word, no demonstrated-capability check behind either                  (S5)
   └─ model_registry_audit reads config, not the usage ledger; ledger schema
      lacks run_id so per-run truth is unjoinable                                (S8)

ROOT D. The cognitive loop has exactly one shape (plan→tools→synthesize)
   ├─ self-analysis questions re-enter the LLM loop                              (S1)
   └─ repair generator: whole-file, single-shot, only-on-red — no incremental
      cycle (test-first, multi-file, chunked)                                    (S7)
```

Confirmed roots: A, B, C, D (all evidenced above by code path + trace/store).
Rejected hypotheses:
- "S3 is a silent bug" — rejected: explicitly documented shadow mode; the
  defect class is C (sensor w/o actuator), not an accidental swallow.
- "S6 is an active writer bug" — rejected: writer already filters code
  fragments (`e1232e0`); the live defect is the unmigrated store + missing
  status/typing (ROOT A residue), not the current writer.
- "S8 models are unlogged" — rejected: traces DO log actual models
  (`model_call_start/end`); the audit simply doesn't read them and the ledger
  can't be joined per-run.

## 3. Falsifying experiments (per root)

- A: construct `ClaimRecord(source.type="log")` with high confidence → today
  `decide()=="save"`. Fix must flip to reject. Negative control: type
  "documentation" stays "save".
- B: build artifacts where fresh `file_read` block > memory block; run trim;
  today fresh block is cut first. Fix: memory-typed blocks enter the same
  budget with priority for fresh reads of the same path.
- C: compare `model_registry_audit.unique_route_models` with the set of models
  in `model_call_start` events of a run where complexity escalation fired —
  today they diverge silently; fixed audit must surface actual-vs-configured.
- D: ask repair generator for a fix with green baseline / oversized file —
  today `no_failing_tests` / `rejected`. (Repairing D is a feature build, not
  a patch; out of scope for cluster 1.)

## 4. Repair plan — one causal cluster per branch/PR

Cluster 1 (CHOSEN): ROOT A write-gate slice — the smallest provable invariant
fix with the highest downstream damage reduction:
  1. fail-before test: `KnowledgeWritePolicy` accepts a log-sourced claim.
  2. patch: `decide()` consults `_NON_ASSERTING_SOURCE_TYPES` (single source of
     truth, minus `memory`? — no: memory/log/tool_output/test_result all
     non-asserting per MIR-054; `file` stays writable but is already filtered
     by `_accept_sentence` + `_is_meaningful_claim`).
  3. negative tests: documentation/web still save; file behaviour unchanged.
  4. targeted + full pytest.
Store migration (the 776+ legacy rows) is Cluster 1b — a separate dry-run
migration script with backup + report, only after gate is fixed (fail-closed
order: stop the inflow, then drain the pool).

Cluster 2: ROOT B — budget symmetry (fresh-read-of-path evicts memory-of-path,
not vice versa). Cluster 3: ROOT C — model audit reads actual usage; add
run_id to ModelUsageRecord. Cluster 4: ROOT D — design work (introspective
answer path; incremental repair cycle), needs operator decisions.

## 5. Tests required (cluster 1)

- test_knowledge_write_policy_rejects_log_sourced_claim         (fail-before)
- test_knowledge_write_policy_rejects_test_result_sourced_claim (fail-before)
- test_knowledge_write_policy_rejects_memory_sourced_claim
- test_knowledge_write_policy_still_saves_documentation_claim   (negative ctl)
- test_non_asserting_types_single_source_of_truth (gate and conflict resolver
  consult the same frozenset)

## 6. Unresolved risks

- Store migration (1b) touches 814-row live JSONL with integrity hashes —
  must reuse the existing state-layer writer, never hand-edit; backup +
  dry-run + idempotence required (per docs/SELF_REPAIR_DOCTRINE.md).
- `role_router.py` tags suggest some flows intentionally store repair lessons
  with `log`-ish provenance — verify no production path legitimately writes
  log-sourced semantic facts before enforcing (checked: the 10 live log-sourced
  rows are all timestamp-restatements, zero information; but re-verify in tests).
- Operator's main checkout is dirty with tooling files; never commit from there.

## 7. Current state / next action

Cluster 1 — MERGED: PR #200 → main `189f9bd` (squash, 2026-07-31T10:54Z).
CI on the PR: Codacy pass, Tests + supply-chain pass, 0 unresolved threads.
Built with fail-before proof:
- 5 new tests failed with `'save' == 'reject'` before the patch (log,
  test_result, memory, code_repository, doctrine-sharing probe); negative
  control (documentation) passed before AND after.
- patch: `core/knowledge_pipeline.py` `KnowledgeWritePolicy.decide` now rejects
  every `_NON_ASSERTING_SOURCE_TYPES` member by assertion class (same frozenset
  the conflict resolver uses — single source of truth); `forum`/`unknown` keep
  the "too weak" rejection; `tool_output` moved to the assertion-class branch.
- tests: targeted `tests/test_knowledge_pipeline.py tests/test_conflict_quarantine.py`
  → 24 passed. Full suite → **6020 passed, 5 skipped** (baseline 6016+3; +6 new
  tests, +2 environment skips: live-store/live-registry tests skip in a clean
  worktree with no `data/`).

Cluster 1b — DONE on branch `fix/memory-pollution-migration` (this branch):
- Two further writer gaps found while building the migration, both proven
  fail-before on live-junk fixtures:
  - `_looks_like_code_fragment` missed statement keywords — the measured 26
    `assert …` rows pass it → added high-precision statement prefixes
    (deliberately NOT "if ", "for ", "pass" — prose opens with those);
  - the write gate accepted `file` claims from CODE files while the conflict
    resolver already excludes them via `_is_code_locator` (MIR-054) → gate
    now rejects code-file claims; prose files (.md/.txt) stay in scope.
- `scripts/migrate_memory_pollution.py` — archives (never deletes) rows
  today's writer would refuse: non-asserting sources, code-file claims,
  code-fragment/mojibake content. Writer signature proved first (tag triple
  + Source line, tag/Source agreement required); everything else fail-closed.
  Dry-run default, lock across read+write, timestamped backups of both
  stores, archive-first write order, idempotent, --json report. Decisions
  imported from the gate itself — report and migration cannot diverge.
- Live dry-run (read-only): 814 rows → **778 would archive** (767 code-file,
  11 non-asserting), **36 kept** — eyeballed: 29 prose (README, docs), 4
  borderline-but-consistent (dir/gitignore locators today's gate still
  accepts), 3 no-Source-line fail-closed incl. both "Bug fixed" lessons.
  Cross-check vs PHASE-1 scan exact: 800 file = 767 code + 33 prose;
  10 log + 1 test_result = 11.
- Tests: 25 migration + 4 writer-gap = 29 new. Full suite **6049 passed,
  5 skipped**.
- Hardening (operator review round 1, fail-before proven — 7 tests failed
  before the fix):
  - writer provenance now requires ``owner == "self"`` (the pipeline calls
    ``remember(..., "agent-auto", "semantic", "self")``); a user-owned row
    wearing the tag triple + a non-asserting Source line is untouched;
  - the archive append is recovery-idempotent: a retry after a crash
    between append and rewrite skips rows the archive already holds
    verbatim (still removing the active copy), and the same ID with
    DIFFERENT content aborts before any backup or write.
  - After hardening: 32 migration tests; full suite **6056 passed,
    5 skipped**; live dry-run unchanged — 814 active / 778 archive /
    36 keep, no record changed class (all live writer rows are self-owned).
- Review round 2 (Codacy + CodeRabbit on the PR):
  - dropped `"del "`, `"finally:"`, `"lambda "` from the statement prefixes —
    prose opens with them (Del Toro / "Finally: …" / "Lambda is an AWS
    service"), the exact false-positive class this list promises to avoid;
    negative prose tests added;
  - the archive append now runs under `state_file_lock(archive_path)` — the
    active store's lock says nothing about the archive file, and
    `PersistentMemoryStore.archive_record` appends under the archive lock;
  - `_backup` centralised as `core.state_integrity.backup_state_file`, both
    migration scripts now share it;
  - unused test variable removed; FABLE_AUDIT merge-state and portable
    apply-command wording fixed.
- `--apply` on the live store NOT run: mutating live memory is an
  operator-gated effect. Command, after merge:
  `python scripts/migrate_memory_pollution.py --workspace <repo-root> --apply`
  (run from the repo checkout whose `data/` holds the live store; the CLI
  exits 0 with "nothing to do" when the store path does not exist, so a
  wrong workspace looks successful while touching nothing)

Cluster 2 — DONE on branch `fix/root-b-memory-in-evidence-budget`
(commit `d597ee7`, PR #202; suite green):
- Defect: `_synthesize` passed only tool artifacts through
  `apply_total_budget`; `<long_term_memory>` was concatenated into the prompt
  outside it, so memory was structurally untrimmable and the fresh read — the
  largest block — was always cut first.
- Fix, two parts:
  - `core/evidence_budget.py`: new `MEMORY_BLOCK_LABEL` and keyword-only
    `trim_first_labels`. Demoted blocks are spent BEFORE any other block
    regardless of size, down to a 50-char content floor; a block that can no
    longer shrink leaves the candidate pool so the loop cannot stall.
  - `core/loop.py::_synthesize`: the memory block enters `raw_blocks` under a
    collision-proof label, is demoted, and is split back out afterwards.
- Fail-before proven: 3 loop-level tests failed on `9fd2800`
  (`TOTAL-BUDGET` absent from the memory block; "no evidence_budget_trim event
  was logged" — the artifacts alone fit the budget, which is the defect
  stated as a measurement).
- Independent verification (autonomous review agent, round 1) found 4 defects
  in that first patch; all four fixed, each with its own fail-before proof:
  1. the candidate filter used the padded notice reserve (120) instead of the
     real notice length (~84), so a block sized in that window was skipped:
     the loop broke and returned OVER budget with `was_trimmed=False` — no
     trim event either. Differential fuzz vs the old algorithm: 192/30 000
     cases where the new code exceeded a budget the old one met. Fixed by
     measuring against the real notice (`_trim_notice`, one definition).
  2. `<allowed_citations>` still advertised memory records the trim had
     removed — before this cluster memory was never trimmed, so the two could
     not diverge. Fixed: only records still in the prompt stay citable.
  3. a character slice cut a record id in half (`- [mem_8357646e3d93d6aa`),
     emitting 155 chars of prompt for zero information. Fixed: whole records
     only, and the block is dropped entirely when none survives.
  4. `trim_first_labels: Iterable[str]` accepted a bare string and exploded it
     into characters (latent). Fixed by type + runtime guard.
- Round 2 of the same review found 6 more, all in the round-1 repair; all
  fixed, four with fail-before proof (the two observability ones are new
  fields, proven by construction):
  1. the citation filter keyed on `kind == "memory"`, which also covers
     WORKING-memory artifacts from prior turns (`obtained_via=
     "working_memory"`, `memory:working_turn_*`). Trimming long-term memory
     therefore revoked the licence to cite the previous turn's own tool
     output — which lives in `<conversation_history>`, outside this budget,
     and was never trimmed. Now keyed on `obtained_via == "memory"`, the axis
     `core/verifier_core.py:53-56` already documents.
  2. the record-id regex `^- \[([^\s|\]]+)` matched any markdown bullet inside
     a record's own content, and the filter compared ids by SUBSTRING: a
     record containing "- [1] …" produced the id "1", which is a substring of
     ~87% of 32-hex ids, making the filter a near no-op. Now anchored
     (`^- \[(mem_[0-9a-f]+) \| tags:`) and compared by equality.
  3. `str.partition` split on the FIRST "TOTAL-BUDGET" marker, so a record
     whose content quoted such a notice discarded the whole memory block
     silently. The repair no longer parses the cut string at all: it measures
     the common prefix against the original.
  4. the repair was line-granular, not record-granular — a multi-line record
     cut inside its third line was still reported as a surviving whole record
     with its tail missing. Record spans are now computed from the original.
  5. the trace still said memory was injected when none of it reached the
     model (`persistent_memory_inject` fires before the budget). Added
     `memory_chars_kept` / `memory_ids_kept` to `evidence_budget_trim`.
  6. the prompt told the model how to cite `<long_term_memory>` even when the
     budget had dropped that block. The clause is now conditional.
- Round 3 found 5 more, two of them in the round-2 repair itself; all fixed,
  five with fail-before proof:
  1. the repair re-derived the cut by walking the two strings, but the notice
     opens with `\n...[` — when the original continued with the same
     characters the scan ran past the cut and mangled the notice, in the worst
     case deleting the words TOTAL-BUDGET and "trimmed to", the only signal
     telling the model its memory was shortened. Measured: ~0.35% of budgets
     for a 5-record block land on such a cut. The cut is now READ from the
     budget's own notice ("trimmed to N of M chars") and cross-checked against
     the block length; a missing or foreign notice fails closed.
  2. the anchored regex still split a record whose CONTENT quoted a
     record-shaped line, truncating it while reporting it whole, and minting a
     phantom id into `memory_ids_kept`. Record boundaries are now accepted
     only for ids the retrieval actually selected.
  3. a record containing a literal `</long_term_memory>` closed the block
     early for the reading model — prompt-structure injection through stored
     text, and the trim path then appended a second tag. The tag is now
     escaped where the block is built (`core/loop_methods2.py`), which fixes
     the untrimmed path too, and the repair no longer appends a tag the
     original did not have.
  4. a record whose text survived in full was still dropped when the cut
     landed exactly on the newline separating it from the next record. Record
     spans now end at the last text character.
  5. STATED, not changed: a single-record block is dropped whole whenever it
     is trimmed at all (the last record's span reaches the closing tag, and
     the budget always reserves ~120 chars for its notice). That is "whole
     records only" applied honestly; the alternative is half a record with a
     citable id. Now documented and covered by a test. (Round 4 accepted this
     call, with one caveat for the audit: with `max_records = 3` a one-record
     retrieval is ordinary, so on those turns the demotion is all-or-nothing —
     stronger medicine than ROOT B asked for. Stated here so it never reads as
     a surprise in a trace.)
- Round 4 found 4 more; all fixed, three with fail-before proof:
  1. a record quoting the header line of ANOTHER RETRIEVED record still
     substituted it: the pattern search cannot tell that quote from the real
     header, so the quoted record was reported as surviving while what the
     model saw was the quoter's paraphrase of it, under the quoted record's
     own citable id. Fixed structurally — record boundaries are no longer
     searched for at all. `_retrieve_persistent` and the prompt builder now
     share one line producer (`memory_record_lines`), and the builder receives
     the ordered `(id, line)` pairs, so offsets are arithmetic and content
     cannot pose as a boundary. The id regex is gone.
  2. the tag escape was one-sided — a record containing the OPENING tag gave
     `open=2 close=1`. Both tags are escaped now, matching the
     `</analysis_target>` precedent the defence cites.
  3. the fail-closed guard did not check the cut length: a notice claiming
     more text than the block holds returned the WHOLE block instead of
     nothing. Now `kept_chars > len(original)` (and a prefix mismatch) fails
     closed like every other unaccountable shape.
  4. an empty `_last_persistent_records` fell back to unfiltered behaviour;
     the fallback is gone — records that do not reproduce the block exactly
     fail closed.
- Tests: 25 new (17 in `tests/test_evidence_budget.py` — 8 budget-policy,
  9 on the memory-block repair; 8 loop-level in
  `tests/test_persistent_integration.py`). Full suite **6083 passed,
  3 skipped** (baseline `9fd2800`: 6058 passed, 3 skipped).
- Observability note: `evidence_budget_trim.total_chars` now COUNTS the memory
  block, so totals either side of this change are not comparable (§1 S2 quotes
  the pre-change field: "total 31 972 chars"). The event also gained
  `memory_trimmed` and `memory_chars`.
- Out of scope, deliberately: the PLANNER prompt (`core/loop.py:991-993`)
  concatenates persistent + experience memory with no total budget at all.
  It has no fresh-artifact competition (tools have not run yet), so ROOT B's
  falsification test does not apply there. Registered here, not fixed.
- REGISTERED, not fixed (needs an operator decision, and the fix lives in a
  different subsystem): `_retrieve_persistent` bumps `access_count` /
  `last_accessed_at` on every retrieved record
  (`core/loop_methods2.py:235-250`), i.e. at retrieval time. Until this
  cluster, retrieved implied
  injected, so the counter measured "the model saw it". Now the budget can
  drop a retrieved record, and the archive scorer's "actively useful" signal
  counts records the model never received. Measured: at a 900-char budget all
  three records reach `access_count=1` while zero reach the prompt. Moving the
  bump after prompt assembly is a memory-subsystem change, deliberately not
  bundled into this cluster.

NEXT ACTION (exact, for any model):
1. Operator (or agent with explicit permission) runs the --apply command
   above; verify with a re-run (idempotence: "would archive: 0") and
   `:memory` retrieval smoke.
2. Cluster 2 — review/merge the branch above.
3. Cluster 3 — ROOT C: add `run_id` to `ModelUsageRecord`; make
   `model_registry_audit` also report ACTUAL models used per run (join on
   run_id or read `model_call_start` events); surface configured-vs-actual drift.
4. Cluster 4 — ROOT D (design, operator decision needed): introspective answer
   path for self-analysis turns (answer from own trace without a fresh LLM
   round-trip), and an incremental repair cycle (test-first, chunked targets).
5. Deferred (operator's earlier list): machine-readable `status=fixed` on
   repair-lesson memory records (the two "Bug fixed" rows).
